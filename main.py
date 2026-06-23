"""
Main Application: 認知的負荷・タイピング動態収集アプリ

タイピング動態とLSL経由の心拍間隔データを同期記録
段階的な認知的負荷タスクを実施する実験用アプリケーション
"""

import sys
import os
import csv
import threading
import time
import random
from typing import List, Tuple, Optional, Dict
from collections import defaultdict
from datetime import datetime

# 【エラー回避】PySide6の監視機能との競合（バグ）を防ぐため、
# 必ず PySide6 よりも "先" に pynput をインポートします。
try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    print("[Warning] pynput がインストールされていません。キーストロークの取得は利用できません")

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QSlider, QSpinBox, QFileDialog,
    QDialog, QDialogButtonBox, QStackedWidget, QScrollArea
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, Slot, QRect, QEvent
from PySide6.QtGui import QFont, QKeyEvent

from pylsl import StreamInlet, resolve_byprop, local_clock

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    print("[Warning] pygame がインストールされていません。オーディオ再生は利用できません")

import config


# ================== オーディオプレイヤー ==================

class AudioPlayer(threading.Thread):
    """Task 2, 3 用のバックグラウンド音声再生
    
    音声ファイルは既に1.5秒間隔で0~9の数字をランダムに流す仕様
    タスク期間中ループ再生する
    """
    
    def __init__(self, audio_file: str, duration_seconds: int = 180):
        super().__init__(daemon=True)
        self.running = False
        self.audio_file = audio_file
        self.duration_seconds = duration_seconds
        
        # pygameの多重初期化によるメモリリークや「既に初期化されています」というエラーを防ぐため、
        # まだ初期化されていない場合のみ初期化を行います。
        if PYGAME_AVAILABLE and not pygame.mixer.get_init():
            pygame.mixer.init()
    
    def run(self):
        """音声ファイルをタスク期間中ループ再生"""
        if not PYGAME_AVAILABLE:
            print("[AudioPlayer] pygame が利用不可です")
            return
        
        audio_path = os.path.join(config.AUDIO_DIR, self.audio_file)
        
        if not os.path.exists(audio_path):
            print(f"[AudioPlayer] エラー: {audio_path} が見つかりません")
            return
        
        try:
            sound = pygame.mixer.Sound(audio_path)
        except Exception as e:
            print(f"[AudioPlayer] エラー: 音声ファイル読み込み失敗 {e}")
            return
        
        print(f"[AudioPlayer] 再生開始: {self.audio_file}")
        
        self.running = True
        start_time = local_clock()
        
        # タスク期間中、音声をループ再生
        while self.running:
            elapsed = local_clock() - start_time
            
            # タスク時間終了で停止
            if elapsed >= self.duration_seconds:
                break
            
            # 音声を再生（ブロッキング）
            sound.play()
            
            # 音声の再生完了を待つ間もフェーズ終了（self.running == False）の要求に
            # すぐに応答できるよう、長くSleepするのではなく小刻みに（0.1秒ずつ）待機して動作のもたつきを防ぎます。
            sleep_time = sound.get_length() if hasattr(sound, 'get_length') else 5.0
            wait_elapsed = 0.0
            while self.running and wait_elapsed < sleep_time:
                time.sleep(0.1)
                wait_elapsed += 0.1
    
    def stop(self):
        """オーディオ再生停止"""
        self.running = False
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.stop()
            except Exception:
                pass
        # self.join(timeout=2)  # UIのフリーズ（2秒間の画面固まり）を防ぐため完了待機は行わない


# ================== ロガー（バックグラウンドスレッド） ==================

class LSLLogger(threading.Thread):
    """
    LSL ハートレート受信ロガー（別スレッドで常時稼働）
    
    メインのUIスレッド（PySide6）をブロックしないように、
    別スレッドでLSLネットワークを監視し、送られてくる心拍間隔（PPI）データを受信し続けます。
    ここで記録されるタイムスタンプは、キーストロークのタイムスタンプと全く同じ
    高精度時計（pylsl.local_clock()）を使用しているため、後から正確な同期が可能です。
    """
    
    def __init__(self):
        super().__init__(daemon=True)
        self.running = False
        self.data: List[Tuple[float, int]] = []  # (timestamp, ppi)
        self.inlet: Optional[StreamInlet] = None
        # UIスレッドからのデータ取得と、このスレッドでのデータ追加が競合しないようにロックを使用します
        self._lock = threading.Lock()
    
    def connect(self) -> bool:
        """
        LSL ストリームに接続します。
        指定された名前（config.LSL_STREAM_NAME）のストリームをネットワーク上から探し出します。
        """
        print("connect開始")
        try:
            print("[LSLLogger] ストリーム探索中...")
            # resolve_bypropは、指定したプロパティ（今回はname）に合致するストリームが見つかるまでブロックします
            streams = resolve_byprop("name", config.LSL_STREAM_NAME, timeout=config.LSL_TIMEOUT)
            if not streams:
                print("[LSLLogger] エラー: LSLストリームが見つかりません")
                return False
            
            # 見つかったストリームの受信用インレットを作成します
            self.inlet = StreamInlet(streams[0])
            print(f"[LSLLogger] ✓ ストリーム '{config.LSL_STREAM_NAME}' に接続しました")
            return True
        except Exception as e:
            print(f"[LSLLogger] エラー: {e}")
            return False
    
    def run(self):
        """LSL データ受信ループ（スレッドのメイン処理）"""
        self.running = True
        
        # LSLの接続処理（connect）はストリームが見つかるまで最大10秒間ブロックするため、
        # UI（画面）全体がフリーズしてしまわないよう、このバックグラウンドスレッド内で実行します。
        if not self.connect():
            print("[LSLLogger] 警告: LSLストリーム接続に失敗しました。モックなしで続行します。")
            
        while self.running:
            if self.inlet is None:
                time.sleep(0.1)
                continue
            
            try:
                # pull_sampleでデータを取得します。データが来ていない場合は最大0.5秒待機します
                sample, _ = self.inlet.pull_sample(timeout=0.5)
                if sample is not None:
                    # 【重要: 同期の要】データを受け取った瞬間にローカルクロックでタイムスタンプを打刻します
                    timestamp = local_clock() 
                    ppi_value = int(sample[0])
                    with self._lock:
                        self.data.append((timestamp, ppi_value))
            except Exception as e:
                print(f"[LSLLogger] 受信エラー: {e}")
    
    def stop(self):
        """ロギング停止"""
        self.running = False
        self.join(timeout=2)
    
    def get_data(self) -> List[Tuple[float, int]]:
        """現在までに蓄積されたデータを取得（スレッドセーフ）"""
        with self._lock:
            return list(self.data)
    
    def clear(self):
        """データをクリア（新しいフェーズ開始時などに使用）"""
        with self._lock:
            self.data.clear()


class KeyLogger:
    """
    キーストロークロガー
    キーストロークロガー (pynput版)
    
    タイピング中のキー入力イベント（押した、離した）を記録します。
    UIイベントフィルタから呼び出され、LSLと同じ local_clock() で打刻することで完全同期を実現します。
    IMEの影響（日本語入力中のイベント吸収など）を完全に回避するため、
    PySide6のUIイベントではなく、OSレベルのグローバルキーボードフックを利用して
    物理的なキー入力（KeyDown/KeyUp）を確実に取得します。
    """
    
    def __init__(self):
        self.data: List[Tuple[float, str, int, str, str]] = []  # (timestamp, event_type, keycode, char, current_phase)
        self._lock = threading.Lock()
        
        # 【要件4: オートリピートの疑似的な除外】
        # pynputにはisAutoRepeatがないため、現在押下中のキーを保持するセットを用意し、
        # すでに押されているキーの連続イベント（Down）は無視する仕組みにします。
        self.pressed_keys = set()
        
        # タスク実行中のみ記録するためのフラグ
        self.is_logging_active = False
        self.listener = None
        self.current_phase_name = ""
        
    # 【目的】現在のフェーズ名をセットする
    # 【役割（Why）】
    # キーストロークデータの各行にフェーズ名を紐づけ、後からデータを分析する際に
    # どのタスク中のタイピングだったかを明確に識別できるようにするため。
    def set_current_phase(self, phase_name: str):
        with self._lock:
            self.current_phase_name = phase_name
    
    def start(self):
        """【要件1】pynput のリスナーを別スレッドで開始"""
        if not PYNPUT_AVAILABLE:
            return
        
        self.listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release
        )
        self.listener.start()
        
    def stop(self):
        """リスナーの停止"""
        if self.listener:
            self.listener.stop()
            
    def set_active(self, active: bool):
        """ロギングの有効/無効の切り替え"""
        with self._lock:
            self.is_logging_active = active
            if not active:
                # 非アクティブになった際に押しっぱなし状態をクリアし、次回開始時に引き継がないようにする
                self.pressed_keys.clear()

    def on_press(self, key):
        """キーが押された時のコールバック（OSレベルの別スレッドで実行）"""
        # 【要件2: LSLタイムスタンプの維持】コールバックが呼ばれた瞬間にLSL時計で打刻
        timestamp = local_clock()
        
        # 【要件3: スレッドセーフなデータ蓄積】PySide6のメインスレッドとの競合を防ぐ
        with self._lock:
            if not self.is_logging_active:
                return
                
            # キーを一意に特定する文字列（例: 'a', 'Key.enter'）
            key_id = str(key)
            
            # 【要件4: オートリピートの除外】すでにセットに存在する場合は無視
            if key_id in self.pressed_keys:
                return
            
            self.pressed_keys.add(key_id)
            
            char = ""
            keycode = 0
            
            # 文字（Char）とキーコードの抽出 (OSの差異を吸収)
            if hasattr(key, 'char') and key.char is not None:
                char = key.char
            else:
                # 特殊キーの場合は 'Key.enter' などの 'Key.' プレフィックスを外す
                char = str(key).replace('Key.', '')
                
            if hasattr(key, 'vk') and key.vk is not None:
                keycode = key.vk
            elif hasattr(key, 'value') and hasattr(key.value, 'vk'):
                keycode = key.value.vk
                
            self.data.append((timestamp, "KeyDown", keycode, char, self.current_phase_name))
    
    def on_release(self, key):
        """キーが離された時のコールバック"""
        timestamp = local_clock()
        
        with self._lock:
            if not self.is_logging_active:
                return
                
            key_id = str(key)
            if key_id in self.pressed_keys:
                self.pressed_keys.remove(key_id)
                
            char = ""
            keycode = 0
            
            if hasattr(key, 'char') and key.char is not None:
                char = key.char
            else:
                char = str(key).replace('Key.', '')
                
            if hasattr(key, 'vk') and key.vk is not None:
                keycode = key.vk
            elif hasattr(key, 'value') and hasattr(key.value, 'vk'):
                keycode = key.value.vk
                
            self.data.append((timestamp, "KeyUp", keycode, char, self.current_phase_name))
    
    def get_data(self) -> List[Tuple[float, str, int, str, str]]:
        """データ取得"""
        with self._lock:
            return list(self.data)
    
    def clear(self):
        """データクリア"""
        with self._lock:
            self.data.clear()
            self.pressed_keys.clear()


class EventLogger:
    """イベント（マーカー）ロガー"""
    
    def __init__(self):
        self.data: List[Tuple[float, str]] = []  # (timestamp, event_name)
        self._lock = threading.Lock()
    
    def log_event(self, event_name: str):
        """イベント記録"""
        timestamp = local_clock()
        with self._lock:
            self.data.append((timestamp, event_name))
        print(f"[EventLogger] {event_name}")
    
    def get_data(self) -> List[Tuple[float, str]]:
        """データ取得"""
        with self._lock:
            return list(self.data)
    
    def clear(self):
        """データクリア"""
        with self._lock:
            self.data.clear()


# ================== UI Views ==================

class CrossView(QWidget):
    """待機・注視画面: 中央に「+」マーク表示"""
    
    def __init__(self):
        super().__init__()
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # ストレッチで余白を確保
        layout.addStretch()
        
        # 中央に「+」マーク
        plus_label = QLabel("+")
        font = QFont()
        font.setPointSize(120)
        font.setBold(True)
        plus_label.setFont(font)
        plus_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(plus_label)
        
        layout.addStretch()
        
        # 下部にフェーズ名と残り時間を表示
        self.info_label = QLabel()
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(12)
        self.info_label.setFont(font)
        layout.addWidget(self.info_label)
        layout.addSpacing(20)
        
        self.setLayout(layout)
    
    def update_info(self, phase_name: str, remaining_seconds: int):
        """フェーズ名と残り時間を更新"""
        self.info_label.setText(f"{phase_name} | 残り時間: {remaining_seconds}秒")


class InstructionView(QWidget):
    """タスク説明・開始待機画面"""
    
    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(50, 50, 50, 50)
        
        self.title_label = QLabel()
        font_title = QFont()
        font_title.setPointSize(24)
        font_title.setBold(True)
        self.title_label.setFont(font_title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        
        layout.addSpacing(40)
        
        self.desc_label = QLabel()
        font_desc = QFont()
        font_desc.setPointSize(16)
        self.desc_label.setFont(font_desc)
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.desc_label)
        
        layout.addStretch()
        
        hint_label = QLabel("Enterキーを押すとスタートします")
        font_hint = QFont()
        font_hint.setPointSize(14)
        font_hint.setBold(True)
        hint_label.setFont(font_hint)
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint_label)
        
        self.setLayout(layout)
    
    def set_instruction(self, title: str, description: str):
        self.title_label.setText(title)
        self.desc_label.setText(description)


# 【目的】ペースト（Ctrl+V等）および右クリックメニューを無効化したテキスト入力欄を作成する
# 【役割（Why）】
# 実験の要件である「コピペ禁止」を完全に満たし、純粋なタイピング動態のみを記録するため。
class NoPasteTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 右クリックメニューの無効化
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def insertFromMimeData(self, source):
        # ペーストイベントを完全に無視する
        pass

class TypingView(QWidget):
    """タイピングタスク画面: 上に原文、下に入力欄"""
    
    def __init__(self):
        super().__init__()
        self.setup_ui()
    
    def setup_ui(self):
        # 【UI改善】画面全体の背景色を落ち着いた色（薄いグレー）に設定し、待機画面とのコントラストを和らげます。
        # オブジェクト名を設定することで、この QWidget にだけスタイルを適用できます。
        self.setObjectName("TypingView")
        self.setStyleSheet("QWidget#TypingView { background-color: #ECEFF1; }")
        
        # 【要件対応】上下セパレートレイアウト（絶対鉄則）
        layout = QVBoxLayout()
        # 画面端の余白と、上下のテキストボックスの間の間隔（Spacing）を設定します。
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)
        
        # 【UI改善】フォントをゴシック体で大きく見やすい 24px に設定します。
        font = QFont()
        font.setFamilies(["Yu Gothic", "Meiryo", "sans-serif"])
        font.setPixelSize(24)
        
        # ==========================================
        # 左側: 読み込みテキスト（お手本用）
        # ==========================================
        self.read_text = QTextEdit()
        self.read_text.setFont(font)
        self.read_text.setReadOnly(True)
        
        # 【操作性改善】テキストカーソル（キャレット）を表示させず、選択等の操作を無効化することで、
        # 被験者の視覚的ノイズを完全に消し去ります。
        self.read_text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        
        # 【UI改善】左側（お手本）用のQSS：背景を薄いグレーにし、「操作できない感」を強調します。
        # 余白（padding）も 20px 設けて見やすくしています。
        self.read_text.setStyleSheet("""
            QTextEdit {
                background-color: #F8F9FA;
                color: #333333;
                border: 1px solid #CCCCCC;
                border-radius: 8px;
                padding: 20px;
            }
        """)
        layout.addWidget(self.read_text, 1)
        
        # ==========================================
        # 下側: 入力テキストボックス
        # ==========================================
        # コピペ禁止クラスを利用
        self.input_text = NoPasteTextEdit()
        self.input_text.setFont(font)
        
        # 【UI改善】右側（入力用）用のQSS：真っ白な背景にし、入力中（フォーカス時）は
        # 青く光る枠線（#4A90E2）で操作対象であることを被験者に直感的に伝えます。
        self.input_text.setStyleSheet("""
            QTextEdit {
                background-color: #FFFFFF;
                color: #000000;
                border: 2px solid #DDDDDD;
                border-radius: 8px;
                padding: 20px;
            }
            QTextEdit:focus {
                border: 2px solid #4A90E2;
            }
        """)
        layout.addWidget(self.input_text, 1)
        
        # ==========================================
        # 【機能追加】スクロール連動機能
        # ==========================================
        # 右側（入力用）のスクロールバーの値が変更された際（改行などで下に行った際）、
        # 左側（お手本用）のスクロールバーの値にその変更を伝播させ、自動で追従してスクロールさせます。
        self.input_text.verticalScrollBar().valueChanged.connect(
            self.read_text.verticalScrollBar().setValue
        )
        
        self.setLayout(layout)
    
    def load_text(self, filepath: str):
        """テキストファイルを読み込み"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
                self.read_text.setText(text)
        except FileNotFoundError:
            self.read_text.setText("[テキストファイルが見つかりません]\n\n" +
                                   "仮のテキストです。実験中はここに被験者が写経する対象テキストが表示されます。")
    
    def block_input(self):
        """入力をブロック"""
        self.input_text.setReadOnly(True)
    
    def allow_input(self):
        """入力を許可"""
        self.input_text.setReadOnly(False)
    
    def clear(self):
        """入力をクリア"""
        self.input_text.clear()
    
    def get_input_text(self) -> str:
        """入力テキストを取得"""
        return self.input_text.toPlainText()


class SurveyView(QWidget):
    """アンケート画面: NASA-TLX + カウント入力（オプション）"""
    
    def __init__(self):
        super().__init__()
        self.sliders: Dict[str, QSlider] = {}
        self.count_spinbox: Optional[QSpinBox] = None
        self.setup_ui()
    
    def setup_ui(self):
        self.setMinimumSize(600, 800)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        main_layout.addWidget(scroll_area)
        
        scroll_widget = QWidget()
        scroll_area.setWidget(scroll_widget)
        
        layout = QVBoxLayout(scroll_widget)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # タイトル
        title = QLabel("アンケート")
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        
        layout.addSpacing(20)
        
        # NASA-TLX スライダー
        for item in config.NASATLX_ITEMS:
            label_widget = QLabel(f"{item['label']}: {item['question']}")
            label_widget.setWordWrap(True)
            layout.addWidget(label_widget)
            
            # スライダーとその左右のラベルを横に並べるためのレイアウト
            slider_layout = QHBoxLayout()
            
            if item['label'] == "作業成績":
                min_text = "(不満) 0"
                max_text = "100 (満足)"
            else:
                min_text = "(低い) 0"
                max_text = "100 (高い)"
                
            min_label = QLabel(min_text)
            min_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slider_layout.addWidget(min_label)
            
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setMinimum(0)
            slider.setMaximum(100)
            slider.setValue(50)
            slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            slider.setTickInterval(10)
            slider_layout.addWidget(slider)
            
            max_label = QLabel(max_text)
            max_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slider_layout.addWidget(max_label)
            
            layout.addLayout(slider_layout)
            
            self.sliders[item['label']] = slider
            
            value_label = QLabel("50")
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(value_label)
            
            slider.valueChanged.connect(lambda val, lbl=value_label: lbl.setText(str(val)))
            layout.addSpacing(30)
        
        # カウント入力欄（必要に応じて表示）
        self.count_label = QLabel("カウント数を入力してください:")
        self.count_label.hide()
        layout.addWidget(self.count_label)
        
        self.count_spinbox = QSpinBox()
        self.count_spinbox.setMinimum(0)
        self.count_spinbox.setMaximum(100)
        self.count_spinbox.setVisible(False)
        layout.addWidget(self.count_spinbox)
        
        layout.addSpacing(20)
        layout.addStretch()
        
        # 次へボタン
        self.next_button = QPushButton("次へ")
        font = QFont()
        font.setPointSize(12)
        self.next_button.setFont(font)
        self.next_button.setMinimumHeight(40)
        self.next_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Enter/Spaceキー連打による誤作動（スキップ）を防止
        layout.addWidget(self.next_button)
        # 注: シグナル接続は goto_phase で行うため、ここでは接続しない
    
    def show_count_input(self, label_text: str = "カウント数を入力してください:"):
        """カウント入力欄を表示"""
        self.count_label.setText(label_text)
        self.count_label.show()
        self.count_spinbox.show()
        self.count_spinbox.setValue(0)
    
    def hide_count_input(self):
        """カウント入力欄を非表示"""
        self.count_label.hide()
        self.count_spinbox.hide()
    
    def get_responses(self) -> Dict[str, int]:
        """アンケート回答を取得"""
        responses = {label: slider.value() for label, slider in self.sliders.items()}
        if self.count_spinbox.isVisible():
            responses['count'] = self.count_spinbox.value()
        return responses


class EndView(QWidget):
    """終了画面"""
    
    def __init__(self):
        super().__init__()
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()
        
        label = QLabel("実験が終了しました。\nお疲れ様でした。")
        font = QFont()
        font.setPointSize(24)
        font.setBold(True)
        label.setFont(font)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        
        layout.addSpacing(20)
        
        info_label = QLabel("データを保存中...")
        font = QFont()
        font.setPointSize(12)
        info_label.setFont(font)
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)
        
        layout.addStretch()
        self.setLayout(layout)


# ================== メインアプリケーション ==================

class ExperimentApp(QMainWindow):
    """
    メインアプリケーション
    
    PySide6のウィンドウを管理し、config.pyで定義されたプロトコル（PROTOCOL）に沿って
    画面（View）の遷移、タイマーの進行、各種ロガーの制御を一元管理します。
    """
    
    # カスタムシグナル
    phase_changed = Signal(int)  # フェーズ変更
    timer_tick = Signal(int)     # タイマー1秒ごと
    
    def __init__(self):
        super().__init__()
        
        # 実験パラメータ
        self.subject_id = ""
        self.phase_index = 0
        self.phase_start_time = 0.0
        self.current_phase: Optional[config.Phase] = None
        self.protocol: List[config.Phase] = []
        
        # ロガー
        self.lsl_logger = LSLLogger()
        self.key_logger = KeyLogger()
        self.event_logger = EventLogger()
        
        # オーディオプレイヤー
        self.audio_player: Optional[AudioPlayer] = None
        
        # アンケート結果
        self.survey_responses: List[Dict] = []
        
        # UI コンポーネント
        self.cross_view = CrossView()
        self.instruction_view = InstructionView()
        self.typing_view = TypingView()
        self.survey_view = SurveyView()
        self.end_view = EndView()
        
        # タイマー
        self.timer = QTimer()
        self.timer.timeout.connect(self.on_timer_tick)
        
        self.setup_ui()
    
    def setup_ui(self):
        """UI セットアップ"""
        self.setWindowTitle("認知的負荷・タイピング動態収集アプリ")
        self.setGeometry(100, 100, config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        
        # 【画面遷移の要】QStackedWidgetを利用して複数のViewを重ねて保持し、
        # setCurrentWidget() を呼ぶことで、見せたい画面だけを最前面に切り替えます。
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self.cross_view)
        self.stacked_widget.addWidget(self.instruction_view)
        self.stacked_widget.addWidget(self.typing_view)
        self.stacked_widget.addWidget(self.survey_view)
        self.stacked_widget.addWidget(self.end_view)
        self.setCentralWidget(self.stacked_widget)
        
        # キー入力検出のために、このウィンドウ全体のイベントをフック（監視）します。
        # これにより、どの画面にいてもキーストロークを漏らさず記録できます。
        self.installEventFilter(self)
        
        # 説明画面のみ Enter キーを監視するためイベントフィルターを残します。
        # TypingView は pynput (グローバルキーロガー) に委譲したためフィルターは不要です。
        # 【要件3: 監視の継続とフォーカス】確実に入力を拾うため、各ビュー（特に文字入力と説明画面）に
        # 対して直接イベントフィルターをインストールします。これで画面が切り替わっても監視が外れません。
        self.typing_view.input_text.installEventFilter(self)
        self.instruction_view.installEventFilter(self)
        
        # アンケート画面の「次へ」ボタンのシグナルは、多重送信（2回進んでしまう等）のバグを防ぐため、
        # 画面遷移のたびに毎回つなぎ直すのではなく、起動時にここで1回だけ接続して永続化しておきます。
        self.survey_view.next_button.clicked.connect(self.on_survey_submit)
    
    def eventFilter(self, obj, event):
        """キーイベント検出"""
        if event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent):
                # 説明画面でEnterが押されたら次のフェーズへ進む
                if self.current_phase and self.current_phase.view_type == "instruction":
                    if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                        self.goto_phase(self.phase_index + 1)
                        return True
        return super().eventFilter(obj, event)
    
    # 【目的】実験を開始し、指定されたパターンでプロトコルを生成・実行する
    # 【役割（Why）】
    # 実験者が選択したラテン方格パターン（A/B/C）に従って、
    # タスクの順序効果を統制した正しいシーケンスをロードして実験を進めるため。
    def start_experiment(self, pattern: str):
        """実験開始"""
        self.protocol = config.get_protocol(pattern)
        
        # LSL ロギング開始 (connect処理はスレッド内で非同期に行われます)
        self.lsl_logger.start()
        
        # pynput ロギング開始
        self.key_logger.start()
        
        # 最初のフェーズへ
        self.goto_phase(0)
        
        # タイマー開始
        self.timer.start(1000)  # 1秒ごと
    
    def goto_phase(self, phase_index: int):
        """
        指定されたフェーズへ遷移する（画面とロジックの切り替え）
        
        引数 phase_index によって protocol の何番目を実行するか決定し、
        そのフェーズの view_type に応じて適切な画面（QStackedWidgetの対象）を表示します。
        """
        # 前フェーズのオーディオを停止
        if self.audio_player is not None:
            self.audio_player.stop()
            self.audio_player = None
        
        # 全フェーズが終了していれば実験完了処理へ
        if phase_index >= len(self.protocol):
            self.end_experiment()
            return
        
        self.phase_index = phase_index
        self.current_phase = self.protocol[phase_index]
        self.phase_start_time = local_clock()
        self.key_logger.set_current_phase(self.current_phase.name)
        
        # イベントログに記録
        self.event_logger.log_event(f"開始: {self.current_phase.name}")
        
        # 【要件3: スレッドセーフなデータ蓄積】
        # タイピングタスク中のみ、グローバルキーロガーでの記録を有効化します
        if self.current_phase.view_type == "typing":
            self.key_logger.set_active(True)
        else:
            self.key_logger.set_active(False)
        
        # ビューの切り替え
        if self.current_phase.view_type == "cross":
            self.stacked_widget.setCurrentWidget(self.cross_view)
            self.cross_view.update_info(self.current_phase.name, self.current_phase.duration_seconds)
        
        elif self.current_phase.view_type == "instruction":
            title = self.current_phase.name
            desc = ""
            if "Task 1" in self.current_phase.name:
                desc = "表示されたテキストを写経してください。"
            elif "Task 2" in self.current_phase.name:
                desc = "表示されたテキストを写経してください。\n\nまた、同時に音声が流れます。\n読み上げられた数字の「7」の回数をカウントしてください。"
            elif "Task 3" in self.current_phase.name:
                desc = "表示されたテキストを写経してください。\n\nまた、同時に音声が流れます。\n読み上げられた数字の「1」と「9」の合計回数をカウントしてください。"
            
            self.instruction_view.set_instruction(title, desc)
            self.stacked_widget.setCurrentWidget(self.instruction_view)
            self.instruction_view.setFocus()
        
        elif self.current_phase.view_type == "typing":
            self.typing_view.clear()
            
            # Task に応じたテキストファイルを読み込み
            if "Vanilla" in self.current_phase.name or "バニラ" in self.current_phase.name:
                text_file = config.TEXT_FILES.get("vanilla", "Vanilla.txt")
            elif "Task 1" in self.current_phase.name:
                text_file = config.TEXT_FILES["task1"]
            elif "Task 2" in self.current_phase.name:
                text_file = config.TEXT_FILES["task2"]
            elif "Task 3" in self.current_phase.name:
                text_file = config.TEXT_FILES["task3"]
            else:
                text_file = "Task1.txt"
            
            text_path = os.path.join(config.TEXT_DIR, text_file)
            self.typing_view.load_text(text_path)
            
            self.typing_view.allow_input()
            
            # 【要件3: 監視の継続とフォーカス】タスク開始時に確実に入力用テキストボックスにフォーカスを強制します。
            # 以前は TypingView 自体にフォーカスを当てていたため、テキストボックスからイベントが漏れることがありました。
            self.typing_view.input_text.setFocus()
            self.stacked_widget.setCurrentWidget(self.typing_view)
            
            # 【要件4: データの揮発防止】全タスクを通して1つのリストに蓄積し続けるため、クリア処理を削除します。
            
            # Task 2, 3 でオーディオ再生開始
            if self.current_phase.has_audio:
                # Task 2 と Task 3 で異なる音声ファイルを使用
                if "Task 2" in self.current_phase.name:
                    audio_file = config.AUDIO_FILE_LEVEL2
                else:  # Task 3
                    audio_file = config.AUDIO_FILE_LEVEL3
                
                task_duration = self.current_phase.duration_seconds
                
                self.audio_player = AudioPlayer(audio_file, task_duration)
                self.audio_player.start()
        
        elif self.current_phase.view_type == "survey":
            # アンケート画面
            # The survey is about the previous phase (the task)
            previous_phase = self.protocol[self.phase_index - 1]

            if self.current_phase.requires_count_input:
                if "Task 2" in previous_phase.name:
                    # Task 2: 音声で読み上げられた「7」の個数をカウント
                    self.survey_view.show_count_input("タスク中に聞こえた『7』の個数を入力してください:")
                elif "Task 3" in previous_phase.name:
                    # Task 3: 音声で読み上げられた「1」と「9」の合計個数をカウント
                    self.survey_view.show_count_input("タスク中に聞こえた『1』と『9』の合計個数を入力してください:")
                else:
                    # Fallback: if requires_count_input is true but we don't have a specific question, hide it.
                    self.survey_view.hide_count_input()
            else:
                self.survey_view.hide_count_input()
            self.survey_view.next_button.setEnabled(True)
            self.stacked_widget.setCurrentWidget(self.survey_view)
        
        elif self.current_phase.view_type == "end":
            self.stacked_widget.setCurrentWidget(self.end_view)
    
    def on_timer_tick(self):
        """
        1秒ごとのタイマー処理
        
        現在のフェーズの残り時間を計算し、0になったら次のフェーズへ自動遷移させます。
        """
        if self.current_phase is None:
            return
        
        elapsed = local_clock() - self.phase_start_time
        remaining = self.current_phase.duration_seconds - int(elapsed)
        
        # Cross View の情報更新
        if self.current_phase.view_type == "cross":
            self.cross_view.update_info(self.current_phase.name, max(0, remaining))
        
        # 時間終了判定（アンケートや説明画面などのユーザー操作待ち画面は除く）
        if elapsed >= self.current_phase.duration_seconds and self.current_phase.view_type not in ["survey", "instruction"]:
            self.event_logger.log_event(f"終了: {self.current_phase.name}")
            
            # Typing View の場合はタイムアップと同時に新たな入力をブロックします
            if self.current_phase.view_type == "typing":
                self.typing_view.block_input()
                self.key_logger.set_active(False)
            
            self.goto_phase(self.phase_index + 1)
    
    def on_survey_submit(self):
        """アンケート提出"""
        responses = self.survey_view.get_responses()
        
        # The survey is about the previous phase (the task), not the current survey phase.
        previous_phase_index = self.phase_index - 1
        if previous_phase_index >= 0:
            task_name = self.protocol[previous_phase_index].name
        else:
            task_name = "N/A" # Should not happen in this protocol
        responses['task_name'] = task_name
        
        # カウント情報（被験者の入力値）の記録
        # 正解数は以下のテキストファイルに記載されています
        # - Task 2: audio/audio_level2.wav_answers.txt（7の正解個数）
        # - Task 3: audio/audio_level3.wav_answers.txt（1と9の合計正解個数）
        if 'count' in responses:
            responses['user_count'] = responses.pop('count')  # ユーザーが入力した個数
        
        self.survey_responses.append(responses)
        self.event_logger.log_event(f"提出: {self.current_phase.name}")
        
        self.goto_phase(self.phase_index + 1)
    
    def end_experiment(self):
        """実験終了"""
        self.timer.stop()
        
        # オーディオ停止
        if self.audio_player is not None:
            self.audio_player.stop()
            self.audio_player = None
        
        self.lsl_logger.stop()
        self.key_logger.stop()
        self.event_logger.log_event("実験完了")
        
        # データ保存
        self.save_data()
    
    def save_data(self):
        """データを CSV ファイルで保存"""
        id_str = self.subject_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 被験者ごとの専用フォルダを作成
        subject_dir = os.path.join(config.DATA_DIR, id_str)
        if not os.path.exists(subject_dir):
            os.makedirs(subject_dir)
        
        # 1. ハートレートデータ
        hr_file = os.path.join(subject_dir, config.HEARTRATE_CSV_TEMPLATE.format(id=id_str))
        with open(hr_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Timestamp', 'PPI'])
            for ts, ppi in self.lsl_logger.get_data():
                writer.writerow([ts, ppi])
        print(f"✓ {hr_file} を保存しました")
        
        # 2. キーストロークデータ
        key_file = os.path.join(subject_dir, config.KEYSTROKES_CSV_TEMPLATE.format(id=id_str))
        with open(key_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Timestamp', 'EventType', 'KeyCode', 'Char', 'Current_Phase'])
            for ts, evt_type, keycode, char, phase_name in self.key_logger.get_data():
                writer.writerow([ts, evt_type, keycode, char, phase_name])
        print(f"✓ {key_file} を保存しました")
        
        # 3. イベントデータ
        evt_file = os.path.join(subject_dir, config.EVENTS_CSV_TEMPLATE.format(id=id_str))
        with open(evt_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Timestamp', 'EventName'])
            for ts, evt_name in self.event_logger.get_data():
                writer.writerow([ts, evt_name])
        print(f"✓ {evt_file} を保存しました")
        
        # 4. アンケートデータ
        survey_file = os.path.join(subject_dir, config.SURVEYS_CSV_TEMPLATE.format(id=id_str))
        with open(survey_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['TaskName', 'UserInputCount', 'MentalDemand', 'PhysicalDemand', 'TemporalDemand', 'Performance', 'Effort', 'Frustration'])
            for resp in self.survey_responses:
                writer.writerow([
                    resp.get('task_name', ''),
                    resp.get('user_count', ''),
                    resp.get('精神的負担', ''),
                    resp.get('身体的負担', ''),
                    resp.get('時間的切迫感', ''),
                    resp.get('作業成績', ''),
                    resp.get('努力', ''),
                    resp.get('フラストレーション', ''),
                ])
        print(f"✓ {survey_file} を保存しました")
        
        print("\n✓ すべてのデータを保存しました")


# 【目的】被験者IDとタスク提示順序のパターンを入力させるダイアログを表示する
# 【役割（Why）】
# 1回の実験ごとに適切なデータを紐づけるため（ID）。
# また、ラテン方格法によるパターン（A/B/C）を実験者が明示的に選べるようにすることで、
# プロトコルのランダム化に伴う「被験者ごとの割り当ての偏りや把握漏れ」を防ぐためです。
def show_startup_dialog(parent=None) -> Tuple[Optional[str], str]:
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QDialogButtonBox, QButtonGroup
    
    dialog = QDialog(parent)
    dialog.setWindowTitle("実験開始設定")
    dialog.setMinimumWidth(350)
    
    layout = QVBoxLayout(dialog)
    
    id_layout = QHBoxLayout()
    id_layout.addWidget(QLabel("被験者 ID:"))
    id_input = QLineEdit()
    id_layout.addWidget(id_input)
    layout.addLayout(id_layout)
    
    layout.addSpacing(10)
    layout.addWidget(QLabel("プロトコルパターン（ラテン方格）:"))
    pattern_group = QButtonGroup(dialog)
    
    radio_a = QRadioButton("A: Task1 -> Task2 -> Task3")
    radio_b = QRadioButton("B: Task2 -> Task3 -> Task1")
    radio_c = QRadioButton("C: Task3 -> Task1 -> Task2")
    
    radio_a.setChecked(True)
    
    pattern_group.addButton(radio_a, 1)
    pattern_group.addButton(radio_b, 2)
    pattern_group.addButton(radio_c, 3)
    
    layout.addWidget(radio_a)
    layout.addWidget(radio_b)
    layout.addWidget(radio_c)
    layout.addSpacing(10)
    
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    
    if dialog.exec() == QDialog.DialogCode.Accepted:
        selected_id = pattern_group.checkedId()
        pattern = "A"
        if selected_id == 2:
            pattern = "B"
        elif selected_id == 3:
            pattern = "C"
        return id_input.text(), pattern
    return None, "A"


def main():
    app = QApplication(sys.argv)

    # メインウィンドウ作成
    window = ExperimentApp()
    window.show()

    # 被験者設定とパターン選択
    subject_id, pattern = show_startup_dialog()
    if subject_id is None:
        # キャンセルされた場合は終了
        sys.exit(0)

    window.subject_id = subject_id

    # 実験開始
    window.start_experiment(pattern)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

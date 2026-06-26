"""
実験プロトコル設定ファイル
認知的負荷・タイピング動態収集アプリ

このファイルは実験の進行順序（プロトコル）、UIの表示設定、
LSL接続情報、出力ファイルのフォーマットなどを一元管理するための設定群です。
"""

from dataclasses import dataclass
from typing import List, Literal


@dataclass
class Phase:
    """
    実験フェーズの定義
    各タスクや休憩時間の1単位（フェーズ）を表現するデータクラスです。
    """
    name: str                          # フェーズ名（画面下部に表示されたり、ログのマーカーとして記録されます）
    duration_seconds: int              # 実行時間（秒）。0の場合はユーザーのアクションで進むまで待機します。
    view_type: Literal["cross", "typing", "survey", "end", "instruction"]  # このフェーズで表示する画面（View）の種類
    log_data: bool                     # この期間のLSLデータ（心拍等）を有効なデータとして扱うかのフラグ
    has_audio: bool = False            # 音声（数字の読み上げ等）をバックグラウンドで再生するかのフラグ
    requires_count_input: bool = False # アンケート画面において、音声タスクのカウント結果を入力させるかのフラグ
    
    @property
    def display_name(self) -> str:
        """画面下部に表示する用のフェーズ名（必要に応じてここで整形できます）"""
        return self.name


# 【目的】ラテン方格法に基づいたプロトコル（フェーズの進行順序）を生成する
# 【役割（Why）】
# タスクの順序効果（疲労や慣れ）を相殺するため。
# パターンA, B, C の3種類をボタンで明示的に選択し、
# どの被験者がどの順序で実施したかを実験者が正確に把握できるようにしています。
def get_protocol(pattern: str) -> List[Phase]:
    """
    pattern: "A", "B", "C" のいずれか
    A: Task1 -> Task2 -> Task3
    B: Task2 -> Task3 -> Task1
    C: Task3 -> Task1 -> Task2
    """
    
    # 共通の事前フェーズ
    pre_phases = [
        Phase("順化中", 3 * 60, "cross", False, False, False),
        Phase("Instruction Vanilla", 0, "instruction", False, False, False),
        Phase("バニラベースライン", 3 * 60, "typing", True, False, False),
        Phase("回復 (Rest 1)", 3 * 60, "cross", True, False, False),
    ]
    
    # 各タスクの定義
    task1 = [
        Phase("Instruction Task 1", 0, "instruction", False, False, False),
        Phase("Task 1: Low (写経)", 3 * 60, "typing", True, False, False),
        Phase("Survey after Task 1", 1, "survey", False, False, False),
    ]
    
    task2 = [
        Phase("Instruction Task 2", 0, "instruction", False, False, False),
        Phase("Task 2: Medium (写経+音声)", 3 * 60, "typing", True, True, False),
        Phase("Survey after Task 2", 1, "survey", False, False, True),
    ]
    
    task3 = [
        Phase("Instruction Task 3", 0, "instruction", False, False, False),
        Phase("Task 3: High (写経+音声)", 3 * 60, "typing", True, True, False),
        Phase("Survey after Task 3", 1, "survey", False, False, True),
    ]
    
    # パターンに応じたタスクの配列
    if pattern == "B":
        tasks = task2 + [Phase("回復 (Rest 2)", 3 * 60, "cross", True, False, False)] + task3 + [Phase("回復 (Rest 3)", 3 * 60, "cross", True, False, False)] + task1 + [Phase("回復 (Rest 4)", 3 * 60, "cross", True, False, False)]
    elif pattern == "C":
        tasks = task3 + [Phase("回復 (Rest 2)", 3 * 60, "cross", True, False, False)] + task1 + [Phase("回復 (Rest 3)", 3 * 60, "cross", True, False, False)] + task2 + [Phase("回復 (Rest 4)", 3 * 60, "cross", True, False, False)]
    else:
        # デフォルトは A
        tasks = task1 + [Phase("回復 (Rest 2)", 3 * 60, "cross", True, False, False)] + task2 + [Phase("回復 (Rest 3)", 3 * 60, "cross", True, False, False)] + task3 + [Phase("回復 (Rest 4)", 3 * 60, "cross", True, False, False)]

    # 共通の事後フェーズ
    post_phases = [
        Phase("実験完了", 1, "end", False, False, False),
    ]
    
    return pre_phases + tasks + post_phases


# UI設定
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900
FULLSCREEN = False  # True にするとフルスクリーン

# タイミング設定（ミリ秒）
KEY_EVENT_POLL_INTERVAL = 10  # キーイベント取得間隔

# オーディオ設定（Task 2, Task 3）
# 注: 音声ファイルは既に1.5秒間隔で0~9の数字をランダムに流す仕様
AUDIO_DIR = "audio"
AUDIO_FILE_LEVEL2 = "audio_level2.wav"  # Task 2 用（7の回数をカウント）
AUDIO_FILE_LEVEL3 = "audio_level3.wav"  # Task 3 用（1と9の回数をそれぞれ別にカウント）

# テキストファイル設定
TEXT_DIR = "texts"
TEXT_FILES = {
    "vanilla": "Vanilla.txt",
    "task1": "Task1.txt",
    "task2": "Task2.txt",
    "task3": "Task3.txt",
}

# データ出力設定
DATA_DIR = "data"
HEARTRATE_CSV_TEMPLATE = "heartrate_{id}.csv"
KEYSTROKES_CSV_TEMPLATE = "keystrokes_{id}.csv"
EVENTS_CSV_TEMPLATE = "events_{id}.csv"
SURVEYS_CSV_TEMPLATE = "surveys_{id}.csv"

# LSL設定
LSL_STREAM_NAME = "PolarPPI"
LSL_STREAM_TYPE = "PPI"
LSL_TIMEOUT = 10  # ストリーム探索タイムアウト（秒）

# NASA-TLX評価項目
NASATLX_ITEMS = [
    {"label": "精神的負担", "question": "このタスクでどれくらい頭を使いましたか？"},
    {"label": "身体的負担", "question": "キー入力などの作業で、身体（指・腕・肩など）をどのくらい使いましたか？"},
    {"label": "時間的切迫感", "question": "「急がなければならない」というプレッシャーをどのくらい感じましたか？"},
    {"label": "作業成績", "question": "指示された通りにタスク（入力・カウント）を遂行できたと思いますか？自分自身のパフォーマンスにどの程度満足していますか？"},
    {"label": "努力", "question": "このタスクをこなすために、精神的・身体的にどれくらい頑張りましたか？"},
    {"label": "フラストレーション", "question": "どれくらいイライラ、焦りを感じましたか？"},
]

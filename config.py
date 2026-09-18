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
    """
    name: str
    duration_seconds: int
    view_type: Literal["cross", "typing", "survey", "end", "instruction"]
    log_data: bool
    has_audio: bool = False
    requires_count_input: bool = False
    text_key: str = None

def get_protocol(pattern: str) -> List[Phase]:
    """
    pattern: "1" ~ "9" のいずれか
    ラテン方格法に基づき、タスク(Low/Med/High)とテキスト(A/B/C)を組み合わせて返す。
    """
    
    # 共通の事前フェーズ
    pre_phases = [
        Phase("事前アンケート", 0, "pre_survey", False, False, False),
        Phase("Instruction Practice", 0, "instruction", False, False, False),
        Phase("タイピング練習", 60, "typing", False, False, False, text_key="practice"),
        Phase("Instruction Vanilla", 0, "instruction", False, False, False),
        Phase("バニラベースライン", 3 * 60, "typing", True, False, False),
        Phase("回復 (Rest 1)", 3 * 60, "cross", True, False, False),
    ]
    
    def create_task(task_type, text_key, order):
        if task_type == "Low":
            task_id = "Task 1"
            has_audio = False
            requires_count = False
        elif task_type == "Medium":
            task_id = "Task 2"
            has_audio = True
            requires_count = True
        else: # High
            task_id = "Task 3"
            has_audio = True
            requires_count = True
            
        return [
            Phase(f"Instruction {task_id}", 0, "instruction", False, False, False),
            Phase(f"{task_id}: {task_type} (Text {text_key})", 3 * 60, "typing", True, has_audio, False, text_key=text_key),
            Phase(f"Survey after {task_id}", 1, "survey", False, False, requires_count),
        ]
        
    patterns_map = {
        "1": [("Low", "A"), ("Medium", "B"), ("High", "C")],
        "2": [("Medium", "B"), ("High", "C"), ("Low", "A")],
        "3": [("High", "C"), ("Low", "A"), ("Medium", "B")],
        "4": [("Low", "B"), ("Medium", "C"), ("High", "A")],
        "5": [("Medium", "C"), ("High", "A"), ("Low", "B")],
        "6": [("High", "A"), ("Low", "B"), ("Medium", "C")],
        "7": [("Low", "C"), ("Medium", "A"), ("High", "B")],
        "8": [("Medium", "A"), ("High", "B"), ("Low", "C")],
        "9": [("High", "B"), ("Low", "C"), ("Medium", "A")]
    }
    
    if pattern not in patterns_map:
        pattern = "1"
        
    tasks = []
    config_list = patterns_map[pattern]
    
    tasks.extend(create_task(config_list[0][0], config_list[0][1], 1))
    tasks.append(Phase("回復 (Rest 2)", 3 * 60, "cross", True, False, False))
    
    tasks.extend(create_task(config_list[1][0], config_list[1][1], 2))
    tasks.append(Phase("回復 (Rest 3)", 3 * 60, "cross", True, False, False))
    
    tasks.extend(create_task(config_list[2][0], config_list[2][1], 3))
    tasks.append(Phase("回復 (Rest 4)", 3 * 60, "cross", True, False, False))
    
    # 共通の事後フェーズ
    post_phases = [
        Phase("Instruction Post-Vanilla", 0, "instruction", False, False, False),
        Phase("ポストバニラベースライン", 3 * 60, "typing", True, False, False),
        Phase("事後アンケート", 0, "post_survey", False, False, False),
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
    "practice": "Practice.txt",
    "A": "TaskA.txt",
    "B": "TaskB.txt",
    "C": "TaskC.txt",
}

# データ出力設定
DATA_DIR = "data"
HEARTRATE_CSV_TEMPLATE = "heartrate_{id}.csv"
KEYSTROKES_CSV_TEMPLATE = "keystrokes_{id}.csv"
EVENTS_CSV_TEMPLATE = "events_{id}.csv"
SURVEYS_CSV_TEMPLATE = "surveys_{id}.csv"
PRESURVEY_CSV_TEMPLATE = "presurvey_{id}.csv"
POSTSURVEY_CSV_TEMPLATE = "postsurvey_{id}.csv"

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

# 認知的負荷・タイピング動態収集アプリ (Cognitive Load & Keystroke Dynamics Collector)

## 1. アプリの目的と概要
このアプリケーションは、PCでの**タイピング動態（キーストローク：キーを押した・離したタイミング）**と、Bluetooth心拍センサー（Polar Verity Sense等）から取得する**心拍間隔データ（PPI/RR間隔）**を、**ミリ秒単位の狂いもなく完全に同期して記録**するための実験用ソフトウェアです。

被験者に段階的な「認知的負荷（例：流れてくる音声をカウントしながら文章を写経するなど）」を与え、その際のタイピングの乱れや自律神経（心拍変動）の反応を収集・分析することを目的としています。

### 💡 なぜ「LSL (Lab Streaming Layer)」を使っているのか？（重要）
通常、キーボードの入力とBluetoothの心拍データを別々のプログラムで保存すると、OSの処理遅延やBluetoothの通信の揺らぎにより、必ず「時間のズレ（同期ズレ）」が発生します。
本アプリでは、LSLという生体信号同期用のネットワーク技術を用いています。中継器がBluetoothデータをLSLネットワークに放流し、メインアプリがそれを受信した瞬間に、**「キーボード入力と全く同じPC内蔵の高精度時計」**でタイムスタンプを打刻します。これにより、後から「どのキーを打っている瞬間の心拍がいくつか」を完璧に突き合わせることができます。

---

## 2. システム要件と必須ライブラリ

実行環境: Python 3.9 以上

以下のコマンドで、必要な外部ライブラリを一括インストールしてください。

```bash
pip install PySide6 pylsl pygame bleak pandas matplotlib
```

### ディレクトリ構成

```
├── main.py                 # メインアプリケーション├── polar_to_lsl.py         # Polar心拍デバイス→LSL 中継器├── lsl_mock.py             # Mock LSL Streamer（テスト用）
├── config.py               # 実験設定・プロトコル定義
├── README.md               # このファイル
├── texts/
│   ├── Task1.txt          # Task 1 用写経テキスト
│   ├── Task2.txt          # Task 2 用写経テキスト
│   └── Task3.txt          # Task 3 用写経テキスト
├── audio/
│   ├── audio_level2.wav            # Task 2 用音声ファイル
│   ├── audio_level2.wav_answers.txt # Task 2 正解カウント
│   ├── audio_level3.wav            # Task 3 用音声ファイル
│   └── audio_level3.wav_answers.txt # Task 3 正解カウント
└── data/
    ├── heartrate_{ID}.csv    # 心拍間隔（PPI）データ
    ├── keystrokes_{ID}.csv   # タイピング動態ログ
    ├── events_{ID}.csv       # イベントマーカー
    └── surveys_{ID}.csv      # アンケート回答
```

### 初期セットアップ

#### 1. 音声ファイルの確認

以下のファイルが `audio/` フォルダに配置されていることを確認してください：

- **audio_level2.wav** - Task 2 用の音声ファイル（1.5秒間隔で0~9をランダムに読み上げ）
- **audio_level2.wav_answers.txt** - Task 2 での正解（7の個数）
- **audio_level3.wav** - Task 3 用の音声ファイル（1.5秒間隔で0~9をランダムに読み上げ）
- **audio_level3.wav_answers.txt** - Task 3 での正解（1と9の合計個数）

#### 2. テキストファイルの準備

各 Task 用の写経テキストを準備してください：

- **Task1.txt** - Task 1（Low 負荷）用の写経テキスト
- **Task2.txt** - Task 2（Medium 負荷）用の写経テキスト  
- **Task3.txt** - Task 3（High 負荷）用の写経テキスト

```bash
# 例：長い日本語テキストまたは英文を各ファイルに配置
# 各 Task で異なる難易度のテキストを準備することを推奨
```

### 実験プロトコル

| フェーズ | 画面 | 時間 | 説明 |
|---------|------|------|------|
| 順化 | Cross View | 3分 | 環境への適応。LSLデータは破棄 |
| ベースライン | Cross View | 5分 | LSLデータ記録開始 |
| Task 1: Low | Typing View | 5分 | 写経のみ（音声なし） |
| Survey 1 | Survey View | - | NASA-TLX 3項目評価 |
| Rest 1 | Cross View | 3分 | 休憩と回復 |
| Task 2: Medium | Typing View | 5分 | 写経 + 音声【audio_level2.wav】再生（1.5秒間隔で0~9をランダムに読み上げ） |
| Survey 2 | Survey View | - | 聞こえた『7』の個数を入力 + NASA-TLX |
| Rest 2 | Cross View | 3分 | 休憩と回復 |
| Task 3: High | Typing View | 5分 | 写経 + 音声【audio_level3.wav】再生（1.5秒間隔で0~9をランダムに読み上げ） |
| Survey 3 | Survey View | - | 聞こえた『1』と『9』の合計個数を入力 + NASA-TLX |
| Recovery | Cross View | 5分 | 最終的なベースライン記録 |

## 3. 実験の実施手順（使い方）

### Step 1: 心拍センサー中継器の起動
まず、Polar心拍センサーを被験者に装着し、緑色のLEDが点灯している（脈を検知している）ことを確認します。その後、ターミナルで以下を実行します。

```bash
python polar_to_lsl.py
```
出力例：
```text
[INFO] Polarデバイスをスキャン中...
[SUCCESS] Polarデバイスに接続しました！
[DATA] 送信中: 850.5 ms (HR: 70 BPM)
```
このように `[DATA]` が流れ始めたら準備完了です。このターミナルは**開いたまま（裏で動かしたまま）**にしておきます。
*(※手元にPolarデバイスがない場合は、代わりに `python lsl_mock.py` を起動すればテストが可能です)*

### Step 2: メインアプリの起動
**別の新しいターミナルを開き**、メインアプリを起動します。

```bash
python main.py
```
- 最初に「被験者 ID」の入力ダイアログが出ます（例：`sub01`）。ここで入力した名前がファイル名になります。
- 自動的に裏で動いているLSLストリームを見つけ出し、実験画面が開きます。
- あとは画面の指示と自動タイマーに従って、被験者にタスクを進めてもらいます。
- 最後の「終了画面」が出た瞬間に、すべてのデータが `data/` フォルダにCSVとして一括保存されます。

---

## 4. 出力されるCSVデータの見方
実験完了後、`data/` フォルダに以下のようなCSVファイルが生成されます。`XXX` は実行時に入力した被験者IDが入ります。

- `keystrokes_XXX.csv`
- `heartrate_XXX.csv`
- `events_XXX.csv`
- `surveys_XXX.csv`

### ① `keystrokes_XXX.csv`（タイピング動態ログ）
| Timestamp | EventType | KeyCode | Char |
|-----------|-----------|---------|------|
| 12345.678 | KeyDown   | 65      | a    |
| 12345.750 | KeyUp     | 65      | a    |
* **意味**: キーボード入力の開始と終了を時系列で記録します。`Timestamp` はアプリ内部で使う同一時計の秒単位タイムスタンプです。
* **分析用途**: KeyDown→KeyUpの差分から「滞空時間（Dwell Time）」を計算し、KeyUp→次のKeyDownの差分から「キー間時間（Flight Time / IKI）」を算出できます。こうした指標は認知負荷状態や集中力の変化を検出するのに有用です。

### ② `heartrate_XXX.csv`（心拍間隔・PPIデータ）
| Timestamp | PPI |
|-----------|-----|
| 12345.800 | 850 |
| 12346.660 | 860 |
* **意味**: PPI は心拍同士の間隔（ミリ秒）です。BPM ではなく、心拍変動（HRV）解析に適した生データです。
* **分析用途**: PPI 時系列から LF/HF、RMSSD、SDNN などのHRV指標を算出して、ストレス・疲労・自律神経の変動を評価します。

### ③ `events_XXX.csv`（イベントマーカー）
| Timestamp | EventName |
|-----------|-----------|
| 12300.000 | 開始: Task 1: Low (写経) |
| 12600.000 | 終了: Task 1: Low (写経) |
* **意味**: 実験のフェーズ開始・終了時刻を記録したしおりデータです。
* **分析用途**: keystrokes / heartrate の連続データを、各フェーズごとに切り出すために使います。

### ④ `surveys_XXX.csv`（アンケート回答結果）
| TaskName | UserInputCount | MentalDemand | Frustration | Effort |
|----------|----------------|--------------|-------------|--------|
| Task 2... | 12           | 80           | 60          | 90     |
* **意味**: 各タスク後に被験者が回答した NASA-TLX の主観スコアに加え、音声タスクで入力したカウント数を保存します。
* **補足**: Task 1 のアンケートでは `UserInputCount` は空欄になります。Task 2 では音声内の「7」の個数、Task 3 では音声内の「1」と「9」の合計個数を記録します。
* **分析用途**: 主観的な認知負荷評価とタイピング・心拍の客観データとの相関分析や、心理生理応答の比較に利用できます。

---

## 5. トラブルシューティング（よくあるエラーと解決法）

#### Q. Polar中継器で `[WARNING] 有効なPPIが取得できません` と出続ける
センサーが肌に密着しておらず、脈波を捉えられていません。デバイスの裏面の緑色のLEDが光っているか確認し、腕にしっかりと巻き付けてください。

#### Q. Polar中継器で `Characteristic ... was not found!` というエラーが出る
PCのBluetoothが、Polarデバイスの特殊なデータ送信機能（PMD）を見失っています。
1. WindowsのBluetooth設定から「Polar Sense XXXX」を削除（ペアリング解除）する
2. PCのBluetoothを一度オフにしてオンにする
3. 再度ペアリングし直す、の手順で解決します。

#### Q. メインアプリで「LSLストリームが見つかりません」と出る
`polar_to_lsl.py`（または `lsl_mock.py`）が起動していないか、ストリーム名が異なっています。必ず**メインアプリを起動する前に**、中継器を起動しておいてください。

#### Q. 長時間タイピングしたのに、キーのデータが少ししか保存されていない
タイピング中に被験者がマウス等で別の場所をクリックし、入力欄（テキストボックス）から**フォーカスが外れてしまった**可能性があります。現行バージョンではフォーカス強制処理を入れていますが、実験中は被験者に「マウスは触らずタイピングに集中してください」と指示してください。

---

## 6. ライセンスと参考文献

- **ライセンス**: 研究用途での使用を想定しています。
- **参考文献**:
  - NASA Task Load Index (NASA-TLX)
  - Keystroke Dynamics as a Biometric Identifier
  - Lab Streaming Layer (LSL) Protocol

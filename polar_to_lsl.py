"""
Polar-LSL 中継器 (polar_to_lsl.py)

Polar心拍センサーからBLE経由でデータを受信し、RR間隔（PPI）を抽出して
LSLにリアルタイムで放流する独立した中継スクリプト。

必須ライブラリ:
pip install bleak pylsl
"""

import asyncio
import time
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from pylsl import StreamInfo, StreamOutlet, IRREGULAR_RATE

# ==========================================
# 設定 (必要に応じて書き換えてください)
# ==========================================
# MACアドレスが明確な場合はここに設定（例: "E7:2E:00:B1:38:96"）
# Noneの場合は POLAR_DEVICE_NAME に合致するデバイスを自動スキャンします
POLAR_MAC_ADDRESS = None 
POLAR_DEVICE_NAME = "Polar Sense" # "Polar" などの前方一致でも可

# PMD (Polar Measurement Data) Service UUIDs
PMD_CP_UUID = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"   # Control Point
PMD_DATA_UUID = "fb005c82-02e7-f387-1cad-8acd2d8df0c8" # Data

# ==========================================

class PolarToLSLBridge:
    def __init__(self):
        # 起動時に1回だけLSLストリームを構築する
        self.outlet = self.setup_lsl_stream()
        self.is_connected = False
        self.last_log_time = 0
        self.log_interval = 2.0  # 数秒に1回程度の標準出力用

    def setup_lsl_stream(self) -> StreamOutlet:
        """
        LSLアウトレット（データ送信口）の初期化
        
        メインアプリ（main.py）側がストリームを見失わないよう、この初期化は
        起動時に1回だけ行い、Bluetoothが切断されても維持し続けます。
        """
        info = StreamInfo(
            name="PolarPPI",  # main.py (config.py) と名前を合わせる
            type="PPI",
            channel_count=1,
            nominal_srate=IRREGULAR_RATE,  # 心拍は規則的ではないため、不定期なイベント駆動型データとして定義します
            channel_format="float32",
            source_id="polar_ble_bridge"
        )
        outlet = StreamOutlet(info)
        print("[INFO] LSLストリームを構築しました...")
        return outlet

    def cp_handler(self, sender: BleakGATTCharacteristic, data: bytearray):
        """
        PMD Control Point からのレスポンス（Indicate）を受け取るハンドラ
        
        ストリーム開始コマンド（0x02, 0x03）を送信した後、デバイスから正常に受け付けられたか
        どうかの応答（0x0F または 0xF0）がここに返ってきます。
        """
        print(f"[DEBUG] CPレスポンス受信 (生データ): {data.hex()}")
        if len(data) >= 4 and data[0] in (0x0F, 0xF0):
            op_code = data[1]
            meas_type = data[2]
            error_code = data[3]
            if error_code == 0x00:
                print(f"[INFO] ストリーム開始成功 (Op: {op_code}, Type: {meas_type})")
            else:
                print(f"[ERROR] ストリーム開始エラー (Code: {error_code})")

    def pmd_data_handler(self, sender: BleakGATTCharacteristic, data: bytearray):
        """
        PMDデータを受信し、PPIをパースしてLSLへ放流するハンドラ
        
        このメソッドは、Polarデバイスからデータが送られてくるたびに非同期で呼び出されます。
        PolarのPMDプロトコルのバイナリ仕様に基づき、バイト列からミリ秒単位の心拍間隔（PPI）を計算します。
        """
        # 初回だけ生の受信データを表示して、本当にデータが来ているか確認する
        if not hasattr(self, '_debug_pmd_printed'):
            print(f"[DEBUG] PMDデータ初回受信: {data.hex()}")
            self._debug_pmd_printed = True

        if len(data) < 10:
            return

        # 先頭バイトが 0x03 (PPI Measurement) であることを確認します
        if data[0] == 0x03:
            # ヘッダーとタイムスタンプ（計10バイト）を読み飛ばしてデータ本体へ
            offset = 10 
            sample_len = 6  # PolarのPPIデータフレームは1サンプル6バイト構成（HR[1] + PPI[2] + Error[2] + Flags[1]）
            
            while offset + sample_len <= len(data):
                # 1バイト目: 参考用の心拍数（BPM）
                hr = data[offset]
                # 2〜3バイト目: PPI（心拍間隔）のミリ秒値（リトルエンディアンで数値化）
                pp_in_ms = int.from_bytes(data[offset+1:offset+3], byteorder='little')
                
                offset += sample_len
                
                # PPIが0の場合（センサーが肌から離れている等）は無効データとしてスキップします
                if pp_in_ms > 0:
                    self.outlet.push_sample([float(pp_in_ms)])
                    
                    # コンソール画面がログで埋め尽くされないよう、出力を数秒に1回に制限します
                    current_time = time.time()
                    if current_time - self.last_log_time >= self.log_interval:
                        print(f"[DATA] 送信中: {pp_in_ms:.1f} ms (HR: {hr} BPM)")
                        self.last_log_time = current_time
                else:
                    # 無効データ（装着されていない等）の場合の警告表示
                    current_time = time.time()
                    if current_time - self.last_log_time >= self.log_interval:
                        print("[WARNING] 有効なPPIが取得できません。センサーが肌に密着しているか確認してください。")
                        self.last_log_time = current_time

    def disconnected_callback(self, client):
        """
        意図せぬ切断が発生した際のコールバック
        
        BLE通信が途絶えた瞬間に呼ばれ、接続フラグをFalseにすることで
        後述の無限ループが再接続プロセスを自動的に開始します。
        """
        print("[WARNING] 接続が切れました。再接続を試みます...")
        self.is_connected = False

    async def run(self):
        """
        メインの無限ループ（自動スキャンと再接続）
        
        実験中にアプリがクラッシュしないよう、あらゆる通信エラーを try-except でキャッチし、
        エラーが起きても5秒待機して再接続を繰り返す「不死身」の構造になっています。
        """
        while True:
            try:
                address = POLAR_MAC_ADDRESS
                
                # MACアドレスが未指定の場合、デバイス名からスキャンする
                if not address:
                    print("[INFO] Polarデバイスをスキャン中...")
                    devices = await BleakScanner.discover(timeout=5.0)
                    for d in devices:
                        if d.name and POLAR_DEVICE_NAME in d.name:
                            address = d.address
                            break
                
                if not address:
                    raise Exception("Polarデバイスが見つかりません。")

                # デバイスに接続
                async with BleakClient(address, disconnected_callback=self.disconnected_callback) as client:
                    self.is_connected = True
                    print("[SUCCESS] Polarデバイスに接続しました！")
                    
                    # BLE通信の安定化のため少し待機（即時コマンド送信による切断対策）
                    await asyncio.sleep(1.5)
                    
                    try:
                        # 1. PMD Data の Notify を有効化
                        await client.start_notify(PMD_DATA_UUID, self.pmd_data_handler)
                        await asyncio.sleep(0.5)
                        
                        # 2. PMD Control Point の Indicate を有効化
                        await client.start_notify(PMD_CP_UUID, self.cp_handler)
                        await asyncio.sleep(0.5)
                        
                        # 3. ストリーム開始要求 (0x02 = Start, 0x03 = PPI)
                        print("[INFO] PPIストリーム開始コマンドを送信します...")
                        await client.write_gatt_char(PMD_CP_UUID, bytearray([0x02, 0x03]), response=True)
                        
                        print("[SUCCESS] データ転送の待機中...")
                    except Exception as e:
                        print(f"[ERROR] ストリーム設定中にエラーが発生しました: {e}")
                        print("\n[DEBUG] --- デバイスが提供している機能の一覧 ---")
                        for service in client.services:
                            print(f"Service: {service.uuid} {service.description}")
                            for char in service.characteristics:
                                print(f"  -> Characteristic: {char.uuid} ({','.join(char.properties)})")
                        print("----------------------------------------------\n")
                        raise
                    
                    # 接続中はループを維持
                    while self.is_connected:
                        await asyncio.sleep(1)
                        
            except Exception as e:
                print(f"[WARNING] 接続エラー/未発見: {e} | 5秒後に再試行します...")
                self.is_connected = False
                await asyncio.sleep(5)

if __name__ == "__main__":
    bridge = PolarToLSLBridge()
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        print("\n[INFO] 中継スクリプトを終了します。")
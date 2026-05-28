"""
Mock LSL Streamer: Polar デバイスの PPI 値をシミュレート

Polarデバイスが手元にない場合や、接続テストを行うためのツールです。
1秒に1回、800ms付近のランダムなPPI値（心拍間隔）を生成し、LSLネットワーク上に送信します。
"""

import time
import random
from pylsl import StreamInfo, StreamOutlet


def main():
    """Mock PPI ストリーム送信"""
    # LSL Stream 情報の設定（main.py の config.py で指定されている名前と一致させます）
    stream_name = "PolarPPI"
    stream_type = "PPI"
    
    # どのようなデータを送信するかという「看板（Info）」を作成します
    info = StreamInfo(
        name=stream_name,
        type=stream_type,
        channel_count=1,
        nominal_srate=1,  # 1 Hz (1秒に1回)
        channel_format="int32",
        source_id="mock_polar_ppi"
    )
    
    # 作成した情報をもとに、データをネットワークへ放流するための「出口（Outlet）」を作成します。
    # main.py の LSLLogger は、これを見つけて接続してきます。
    outlet = StreamOutlet(info)
    
    print("=" * 60)
    print("Mock LSL Streamer - Polar PPI Simulator")
    print("=" * 60)
    print(f"Stream Name: {stream_name}")
    print(f"Stream Type: {stream_type}")
    print(f"Source ID: mock_polar_ppi")
    print()
    print("1秒ごとにランダムなPPI値（750～850ms）を送信中...")
    print("終了: Ctrl+C を押してください\n")
    
    try:
        while True:
            # 800ms付近のランダムなPPI値を生成
            # PPI (Peak-to-Peak Interval) は心拍間隔（ミリ秒）のことです
            ppi_value = random.randint(750, 850)
            
            # LSL ストリームに送信します（即座にネットワーク上のすべての Inlet へ届きます）
            outlet.push_sample([ppi_value])
            
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] PPI: {ppi_value} ms")
            
            # 1秒待機
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\nMock LSL Streamer を停止しました")


if __name__ == "__main__":
    main()

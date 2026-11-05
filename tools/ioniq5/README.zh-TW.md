# IONIQ 5 CAN FD、comma four、Mac Jetlink、TURZX 停車與離線測試

這是 Henry HCOP 的台架分支。裝置啟動入口在 AGNOS 上會直接結束，以免現有 C4 進入不同版本的系統更新。HCOP 基線取自 henryopen/openpilot 的 1283b4d3，Carrot 協定與測試資料格式固定在 FANHAOHSIANG/openpilot-carrot 的 334a4ac5。原始行車資料和配對資訊只留在本機。

## 在現有 carrot-wip 上取樣

維持原有 C4 安裝，車輛 READY、P 檔、車速為零，Mac 及 HUD 照平常接好。在 C4 的 SSH 終端執行以下命令。正式的停車工具只訂閱現有服務、讀取 Params 與 Jetlink 狀態。每個 CAN 項目只列 bus、位址和長度。

```bash
cd /data/openpilot
curl -fLsS https://raw.githubusercontent.com/FANHAOHSIANG/sunnypilot/test/hcop-ioniq5-c4-jetlink-hud/tools/ioniq5/parked.py -o /tmp/hcop-parked.py && \
  PYTHONPATH=/data/openpilot python3 /tmp/hcop-parked.py --seconds 20
```

如需在電腦進一步比對，可在 python 指令尾端加上 `> /tmp/hcop-parked.json`，再把該檔複製到電腦。報告區分 Mac 身分、N16 已識別、外部推論當下正在使用。P 檔時外部模型可能待命。導航計數只代表取樣期間實際收到的事件。若韌體數為 0，需另取得自原廠系統完整開機的被動紀錄。這支工具不會主動查詢或重啟 ECU。

## 電腦端重現

用含 Python 3、numpy、pytest、pycapnp、Pillow 和 zstandard 的環境，在這個分支的根目錄執行。第一次測試時需另外抓取固定版本的 Carrot 參照和 HCOP 的 opendbc。

```bash
git submodule update --init --checkout tools/ioniq5/carrot-reference opendbc_repo
python3 -m pytest -c /dev/null --confcutdir=tools/ioniq5 tools/ioniq5/test_compat.py
PYTHONPATH=opendbc_repo python3 tools/ioniq5/native_fingerprint.py /path/to/hcop-parked.json
python3 tools/ioniq5/replay.py /path/to/rlog.zst
```

`native_fingerprint.py` 固定車名後，將觀察到的 CAN 指紋和 ECU 類別交給 HCOP 自己的 Hyundai 介面，比對 alpha 縱向開與關兩種原生結果。這是參數推導測試。行車中的封包可能已受到當時軟體影響，不能藉此確定原廠啟動時的配置。

`replay.py` 使用 Carrot 的 schema 解讀行車紀錄，以原始 bus 和時間戳測試 HCOP 顯示雷達、胎壓和彎道演算法。通過新鮮度、縱向啟用、踏板與變換車道條件的模型幀才納入彎道比較。數值是離線計算的加速度上限。遇到截斷的最後一筆訊息會標示 incomplete（complete=false）並回傳 2。

如需核對 Carrot「主動發送」的 bus、位址、DLC，可在電腦編譯 HCOP 原生 allowlist 檢查器，再套到同一段 rlog。參數取自前一步 HCOP 自己的 native_fingerprint 結果。以下的 13 僅對應本次行車取樣與 alpha 測試組態。

```bash
cc -std=gnu11 -DALLOW_DEBUG -I opendbc_repo tools/ioniq5/tx_layout.c -o /tmp/hcop-tx-layout
/tmp/hcop-tx-layout 13 > /tmp/hcop-tx-layout.txt
python3 tools/ioniq5/replay.py --tx-layout /tmp/hcop-tx-layout.txt /path/to/rlog.zst
```

`outside_tx_layout` 表示某個直接發送組合不在這份測試組態的允許清單內。原廠封包經 Panda 橋接是不同的路徑。比對只檢查位址、bus 和 DLC，沒有判定封包內容、ECU 接收結果或 AEB 的可用性。

## Mac 模型與 HUD 的測試範圍

- N16 模型轉接器使用固定模型規格與 HCOP 原生 Parser，接受完整輸出及端點已協商的精簡輸出。這一版尚未把模型輸出接進原生 HCOP 的行車 modeld。
- HCOP 的 HUD 伺服器針對 IONIQ 5 訂閱被動 CAN，從 HCOP 車型物件取得 A-CAN/E-CAN。前雷達、角雷達需完整報文群通過連續觀測門檻，胎壓直接取自 CAN FD TPMS。這些結果只用於顯示。
- `hud_bridge.py` 從標準輸入接受 HCOP 的 SSE 顯示資料，在 Mac 端產出 Carrot HUD 可讀的二進位封包。封包提供即時車速、巡航、左右方向燈、可用胎壓和觀測到的雷達點。設定採用 ClusterHud=1、Orientation=2、ScreenMode=7，另可由 `--nav-preview` 提供現有的 256×256 導航預覽。測試輸出需使用新的暫存路徑，程式不連面板，也不會覆蓋正在運作的 Jetlink App 封包。
- Google 分享、Mapbox 路由與 NOO 仍屬於 Carrot 環境。模擬模型封包與可讀的 HUD 封包不能驗證導航控制、USB 實際延遲、面板實際刷新、FCA/AEB 或方向燈執行器。

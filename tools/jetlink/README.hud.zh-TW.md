# hkg Jetlink → Carrot Mac HUD：第一版測試移植

## 修正點擊崩潰並恢復 HUD 開關（2026-10-23）

實機 swaglog.0000001022 與 1026 均記錄 UI 點擊回呼在讀取 `IsOnroad` 時
拋出 `openpilot.common.params.UnknownKeyName: b'IsOnroad'`。這證明該次點擊造成 UI 崩潰，
不能單憑此紀錄推論整機是否也重啟。錯誤發生於啟用標記寫入之前。
此分支沒有註冊 `IsOnroad`；manager 實際維護的是 `IsOffroad = not started`。
本版改以 `IsOffroad` 判斷是否允許操作，缺值時禁止；Params 讀取異常轉為可處理的設定錯誤。
背景 publisher 同樣改讀 `IsOffroad`，避免成功開啟後再因相同錯誤退出。
給 Mac 的封包仍保留 Carrot 格式的 `IsOnroad` 欄位，它不是 C4 Params 查詢。
恢復 Jetlink 旁的原生 `HUD` 按鈕，UI 另以 `ui_state.started` 阻擋行車中點擊。
本次有明確錯誤堆疊與回歸測試，但修正版仍待 C4 實機確認；早期僅滑動的回報不由此堆疊直接證明。

基底：`FANHAOHSIANG/sunnypilot` 的 `hkg-enhanced-jetlink`
`b2d7f2893ce76231a8de7014379c676ecf8b542e`。獨立分支：`hkg-enhanced-jetlink-hud`。
2026-10-23 已合併 `hkg-enhanced-jetlink` 的 `d87715b5f`：Mac protocol 3
採用精簡模型回覆，循環隱藏狀態留在 Mac；保留 HUD 傳輸及預設關閉設定。
這是可供停車驗證的原始碼候選；沒有遠端部署到 C4 或 Mac。

## 本版功能

- 讀取車速／設定速度、方向燈／盲點、巡航狀態、實際 `carControl.latActive`（含 MADS）、
  警示文字、模型路徑／車道線／道路邊緣、radarState 前車資料。
- 沿用 Carrot renderer 與 Mac H.264 / TURZX 顯示輸出；hkg 資料轉成有上限的 JSON，
  不把不同 fork 的 cereal 二進位資料互相解碼。
- 使用已存在的 `carrot_hud_v1` / `0x4000`。Mac App 必須包含 Carrot HUD receiver。
- 預設關閉。Mac 宣告 HUD 能力後啟動低優先序待命程序；開關即時控制資料傳送。
  關閉時不訂閱／編碼車況，清除暫存資料；再次開啟會重新訂閱，不需重設 USB 或重啟 C4。
- 最多 5 Hz、每個 JSON 最多 14 KiB，保留最新一包。實際 FunctionFS 傳輸還有 framing/alignment。
- 閒置時由 jetlinkd 傳送；USB lease 交給 modeld 後，由 DirectClient 在模型回覆完成、
  沒有 pending inference、且當幀至少剩 3 ms 時嘗試送 HUD。資料序號使用原 client。
  `try_send` 遇到佇列壓力略過，不另外開 USB、不讀取／消耗 HUD acknowledgement。
- 停用或資料過期不重播舊資料。Mac 接收封包超過 0.5 秒失效；各車況來源也個別檢查時效。
  可用來源改變會清除 renderer parser 的路徑／前車／啟用狀態快取。

相機、導航指示、地圖、高德投影、iOS HUD 未包含在第一版。
資料來源失效時顯示等待／HUD data unavailable；這不是取代 C4 原本的駕駛警示。
沒有修改 CAN、Panda、轉向、縱向、模型契約、大小模型交接條件。
USB 額外負載仍需實測，不能把離線測試視為即時性保證。

## 安裝順序（停車測試）

1. 先在 Mac 使用伴隨分支：
   https://github.com/FANHAOHSIANG/carrotMAC-HUD/tree/hkg-jetlink-hud
   退出 Jetlink 與舊 helper，執行 `06-HKG-HUD.command`。
   它檢查既有 renderer 固定版本、Mac 適配及 App receiver，備份並安裝 adapter；
   不替換 App，不下載／更換模型。檢查不通過則保留現有安裝。
2. 在 C4 停妥、熄火且保持供電時檢查 `git status --short`。
   若有修改先保存並確認用途；不要強制 reset、checkout 或覆蓋。
   取得本測試分支並切換後，先讓正常編譯完成，再檢查 Jetlink 關閉 HUD 時仍正常。
3. 更新程式後重啟一次，進入「設定 → 功能開關」，在 Jetlink 旁找到 `HUD`。
   停車 offroad 可點擊切換，之後開關即時生效，不需重啟。也可使用指令：

```sh
cd /data/openpilot
python tools/jetlink/hud_config.py --enable
```

指令介面提供 `--enable` / `--disable` / `--status`，與 UI 使用同一個啟用標記。

4. 啟動原有 HUD-enabled Jetlink App。依原有流程確認大模型 Active，
   並確認 Mac 上 `hkg_hud.py` 啟動。`--status` 只能確認設定與 publisher 檔案存在，
   不能證明螢幕已顯示或大模型正在被採用。

## 最小驗證

- 在同模型、同解析度比較 HUD 關／開的推論 p50/p95/p99、frame drop、連線重試及小模型回退。
- 停車確認轉向燈、巡航設定速度、MADS/latActive 與 C4 畫面一致。
- 用離線資料或回放確認車速與路徑、雷達左右座標；不要在道路上製造切入／急煞測試。
- 停車拔除／重接 Mac USB，確認資料失效且無凍結前車／啟用狀態；大小模型交接仍遵守原限制。
- 關閉 Mac helper 或拔掉 TURZX 不應改變模型身分或控制狀態。HUD 負載造成超時／回退即停測。

## 還原

停車 offroad 執行 `python tools/jetlink/hud_config.py --disable`，無須重啟。
這控制資料傳送，不是螢幕電源；
Mac 既有畫面會在資料過期後進入無資料狀態。重新開啟後，連線正常即可恢復。
首次安裝這版程式仍需重啟一次，讓正在執行的 UI／Jetlink 載入新程式。
需要完全還原時切回原 `hkg-enhanced-jetlink`（先確認工作目錄無未保存修改）。
Mac 退出 App 後在伴隨分支執行 `python3 scripts/enable_hkg.py --rollback`。
HUD 安裝不替換原 Jetlink App；它的模型選擇與 App 版本保持原狀。

## 驗證範圍

離線測試涵蓋欄位／單位、MADS、有限數值、資料大小與過期、USB 背壓與序號、
無 HUD 回覆下連續模型輸出、原有 USB lease／清理／模型契約回歸。
本版 C4 端 55 項離線測試通過（test_direct、test_pipeline、test_hud、test_hud_toggle、test_hud_params），
包含精簡回覆與 HUD 共用序號、無 HUD acknowledgement 下的連續模型輸出。
另涵蓋關→開→關→開的資料更新、停用時取消訂閱、重新開啟取得新資料，
以及指令共用設定與行駛中鎖定。
參數相容性測試使用實際 Python Params 的 check_key/get_bool 與本分支 params_keys.h，
僅替換 native backend；重現舊 `IsOnroad` 的 UnknownKeyName，驗證修正後 UI 共用 setter 與 CLI 可開關。
publisher 生命週期測試檢查真實註冊鍵，並驗證停車／行車的封包旗標。
另有 6 項 UI 測試使用實際原生元件、標籤、捲動與點擊流程，包含參數讀取失敗時不退出 UI。
UI 測試的參數替身依本分支註冊表拒絕未知鍵；先前允許任意鍵的替身漏掉了這次實機錯誤。
圖形呼叫、字型／資產與車況 IPC 仍使用替身，沒有執行 C4 GPU／觸控實機驗證。
先前 Mac 端 8 項測試通過（含實際 parser/live-source 的快取清除）；本次未修改 Mac 端。
另以 pinned Carrot parser 驗證合成的 20 m/s 車速 → 72 km/h、設定 90 km/h、
17 點路徑與前車、latActive=true。這是合成資料測試，不是實車紀錄或完整畫面測試。
尚未執行實體 C4/Mac/TURZX 顯示與延遲測試，也沒有執行 C4 完整 ARM64 編譯。

```sh
PYTHONPATH=third_party/jetlink python -m unittest discover -s openpilot/selfdrive/modeld/jetlink/tests -p 'test_hud.py' -v
PYTHONPATH=third_party/jetlink python -m unittest discover -s openpilot/selfdrive/modeld/jetlink/tests -p 'test_hud_toggle.py' -v
PYTHONPATH=.:third_party/jetlink python -m unittest discover -s openpilot/selfdrive/modeld/jetlink/tests -p 'test_hud_params.py' -v
PYTHONPATH=third_party/jetlink python -m unittest discover -s openpilot/selfdrive/modeld/jetlink/tests -p 'test_direct.py' -v
PYTHONPATH=third_party/jetlink python -m unittest discover -s openpilot/selfdrive/modeld/jetlink/tests -p 'test_pipeline.py' -v
PYTHONPATH=.:third_party/jetlink python -m unittest discover -s tools/jetlink/tests -p 'test_hud_ui.py' -v
```

本版參考 Carrot `7e371b92` 的 HUD publisher/receiver 分層與 `carrotMAC-HUD`
main `15cc1a71`，並保留其 MIT 授權來源；不是把 Carrot 的控制或 USB daemon 覆蓋到 hkg。

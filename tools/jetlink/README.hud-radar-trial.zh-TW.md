# 持續運作版本

HUD 開啟且 Jetlink HUD 連線存在時，發布程序自動啟動被動雷達讀取，關閉 HUD 或結束連線即停止。無需 SSH、無測試倒數；重新連線會重新建立。僅限已驗證車型與 A-CAN bus，不傳送 CAN，不變更 ECU、Panda 或控制資料。資料仍依原始 CAN 時間過期，過期目標會移除。讀取失敗每 5 秒重試，不影響其他 HUD 車況。

更新後停車重新啟動 C4 一次，Mac 不需更新。手動實測工具仍可用於診斷，其檔案不再是持續運作的資料來源。

已完成啟停、記憶體快照及過期測試；持續版本尚待實車確認。下方為原先限時診斷工具說明。

# Ioniq 5 HUD 雷達多目標實測

這版補上 Carrotpilot 的三組 CAN FD 前雷達及兩組角雷達目標解碼。
解碼結果只加入送往 Mac 的 HUD JSON，不修改車輛使用的 `radarTracks`、
`radarState`、縱向／橫向控制、Panda、ECU 或 CAN 發送內容。
預設不執行實測，不會在開機時自動啟用任何雷達。

## 更新與測試

在 C4、車輛停妥時執行，保留原有三個 CommaView 本機修改：

```bash
(
  set -e
  cd /data/openpilot
  test "$(git branch --show-current)" = "hkg-enhanced-jetlink-hud"
  git fetch origin hkg-enhanced-jetlink-hud
  git merge --ff-only FETCH_HEAD
  git log -1 --oneline
)
```

更新成功後，C4 重啟一次以載入新版 HUD publisher。Mac 原本的 HKG adapter
已能讀取這些欄位，本次不用重裝或更新 Mac App。
不要用 `git reset --hard` 或清除本機 UI 修改。

待 C4 完成車型辨識、既有 HUD 開關開啟，車輛 READY、P 檔、停妥，執行：

```bash
cd /data/openpilot
python tools/jetlink/hud_radar_trial.py --publish --seconds 120
```

120 秒後自動停止；Ctrl+C 可提前停止，停止後撤除新增資料，HUD 回復原生資料來源。
首次安裝需要重啟 C4；之後每次開始／停止試驗不需重啟。
不加 `--publish` 則只解碼與列出診斷，不送到 HUD。
可用時間為 5–600 秒。不要在操作終端機時駕車。

## 看哪些結果

- `A-CAN bus`：由目前 Ioniq 5 CarParams 與 HKG CanBus 計算，不固定假設 bus 1。
- `deviceState.started`：openpilot 的運作狀態，不等同儀表 READY 的直接讀值。
- `原生目標`：HKG 原本 `radarTracks` 的目標數。
- `front210/front3a5/front400`：前雷達格式；`corner235/corner180`：角雷達格式。
- `解碼目標`：通過完整群組、連續觀測、位置與時效檢查的目標；左／中／右只是側向位置分類。
- `HUD已送出`：C4 最新 HUD 封包內的實測目標數，**不等同 Mac 已收到或螢幕已顯示**。
- 結束後列出各 bus 收到的候選報文次數與位址數；不同 bus、長度不符或不完整群組不會拿來輸出。

若有解碼目標但 HUD 已送出持續為 0，先確認更新後已重啟、HUD 已開啟，
且 carState 新鮮；正常傳輸仍受既有 14 KiB 封包預算限制。
若 C4 已送出但 Mac 無顯示，貼回本工具輸出及 Mac HUD 診斷。
若仍為 0，也請貼回結尾群組／bus／位址數，本版不會為了產生目標而自動發 CAN 指令。

## 邊界與驗證

只接受 `HYUNDAI_IONIQ_5` CAN FD。支援 0x210–0x21f（32 bytes）、
0x3a5–0x3c4（24 bytes）、0x400–0x41d（24 bytes）、
0x235–0x248（32 bytes）、0x180–0x184（32 bytes）。
完整群組與長度只能降低誤判，不能取代本車實測。多組前雷達格式同時出現會停止
輸出前雷達目標。未確認的 0x430/0x440 range bins 不當成車輛。

CAN 時間戳與群組內最舊量測決定時效；資料超過 0.25 秒停止採用，
不因重寫檔案刷新舊目標。HUD 封包到 Mac 仍有既有傳輸及失效時間。
首次需要三個完整連續群組及目標連續觀測；不完整群組會清除對應輸出。
最多輸出 32 個目標，缺少新鮮 ego speed 不加上實測目標。
角雷達 x=0 的並排目標保留真實側向位置，不捏造前方距離。

來源固定為 ajouatom/openpilot `a564ce1dc082909b1fdc73b72e64d920336b34d6`：
`opendbc_repo/opendbc/car/hyundai/radar_interface.py`、`radar_group3.py`、
`opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd_radar.py` 與
`hyundai_canfd_corner_radar_{235,180}.dbc`。MIT 授權位於
`openpilot/selfdrive/modeld/jetlink/HUD_RADAR_LICENSE`。

離線驗證：獨立 cantools 以原版 DBC 編碼合成測試向量，再交給本解碼器；
涵蓋五組格式、左右符號、速度、空目標、錯誤 bus／DLC、過期、格式衝突、
Group 3 換槽身份、boot 隔離、原生資料去重及停止後回復。
另以既有 Mac adapter 和固定 Carrot parser／scene builder 驗證左右車輛與主前車。
這些是合成資料與軟體測試，沒有宣稱已在使用者實車或 USB 螢幕上驗證。

```bash
PYTHONPATH=. python -m unittest openpilot.selfdrive.modeld.jetlink.tests.test_hud_radar_trial -v
```

實測程式無需安裝 cantools；測試向量已固定存檔。

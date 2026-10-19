# sunnypilot + Mac Jetlink 實驗分支

本分支以 TonyBinheWu/sunnypilot `master` 的
`c6f579b270ec04cb2f50901cd5d7187614af41c3` 為基底。
Jetlink runtime 取自 ajouatom/openpilot carrot-wip
`ba186915179816a9b0476cec52bf321e73d2d4b0`；原有 MIT 授權保留於
`third_party/jetlink/LICENSE`，上游版本紀錄見 `UPSTREAM.md`。

**狀態：程式整合完成，尚未完成 C4 / Mac 實機驗證，不能視為已可上路的版本。**

## 第一版支援範圍

- C4 執行 sunnypilot 的預設（stock）模型執行器。
- Mac Jetlink 使用 protocol 3，預先載入 Cinque v2 queued ONNX 大模型。
- 指定模型 SHA256：`09d080f36965bb2a0790500452bd328aa03c484d0222aa79d1ad9f021a522aec`。
- USB 3 SuperSpeed 實體資料線；沒有網路模式或手機網路設定。
- sunnypilot 原本的控制器、DesireHelper、RELC、車輛介面與模型訊息發布邏輯保留。
- 沒接 Mac 時使用原本的 C4 模型；啟用 Jetlink 的開關預設為關閉。

**目前不支援：** sunnypilot 自訂模型的 modeld_v2 / split / multi-policy、
Chestnut 與 Jetlink 同時使用、RL Nav、任意模型切換、外接 HUD／地圖影像。
選到不支援的本地模型時保留其原生執行器，不強制覆蓋選擇。
模型 SHA 相同但輸入、輸出、大小或 checkpoint 不同，也會拒絕接入。

## 停車檢查流程

1. 使用本分支完成正常的 sunnypilot 編譯／啟動。
2. C4 的 Models 頁面將 Small Model 選成 Default，移除 Chestnut 裝置。
3. 在 Models 或 Toggles 開啟 **Jetlink Mac (Experimental)**，重新啟動。
4. 實際關閉點火；單純啟用 always-offroad 不算完成連線準備。
5. Mac 開啟支援 protocol 3 的 Jetlink，選擇並完成上述 Cinque v2 模型準備。
   這個分支不會幫 Mac 下載、轉換或更新模型，也不修改你現有的 Mac 安裝。
6. 使用 USB 3 資料線連接，保持車輛停止且控制未啟用，等待 Models 顯示
   `Active — Mac inference, Cinque v2`。`ready` 只代表 USB daemon 就緒。
7. 若顯示 `waiting`、`retrying`、`unsupported` 或 `blocked`，先保留狀態資訊供排查。
   不要靠忽略錯誤來啟用控制。

Mac App 找不到該 SHA256 的 Cinque v2 時，現有模型不能直接替用；需要另外準備
相符的 ONNX 匯出。既有 RL Nav 模型即使也是 protocol 3，仍不符合本版條件。

## 斷線與切換行為

- 原生模型先執行至少三次，再準備可選的相機 warp。
- 接入 Mac 只允許在 carState、selfdriveState、carControl 都有效且更新未超過
  250 ms，車速接近零、standstill 且橫向／縱向控制均未啟用時發生。
- warp 編譯在獨立子程序進行；失敗時繼續使用原生模型。
- 成功接入後，行駛本身不會觸發斷開。每個 IPC 推論要求有總共 150 ms 的上限，
  並核對 frame ID、回覆大小與有限數值。
- 活躍推論失敗會鎖存 commIssue 接手事件，清空本地模型歷史，略過失敗畫面，
  然後恢復原生模型輸出。它不是已驗證的無縫切換。
- 錯誤不會因五秒時間到了就自行解除；必須重新取得上述停止、解除控制的狀態。
- USB 重新設定／引擎握手只在實際 offroad 進行；清理後仍有執行緒或 UDC 佔用時
  停止重綁並顯示 blocked，需在 offroad 排查或重新啟動。

## 狀態與檢查

```sh
cat /dev/shm/sunnypilot-jetlink.json
cat /dev/shm/sunnypilot-jetlink-model.json
```

第一個檔案為 USB / Mac daemon 狀態；第二個檔案的 `active: true` 才代表
modeld 正在使用 Mac 的結果。時間戳記是該裝置的 monotonic time。

本機離線測試：

```sh
DEV=CPU python -m unittest discover -s openpilot/selfdrive/modeld/jetlink/tests -v
git diff --check
```

2026-10-20 驗證：25 項通過，1 項 CPU warp 像素檢查因環境沒有 clang 而跳過。
已驗證：固定模型契約、sunnypilot 真實 parser 的輸出形狀、protocol 3 上游
請求／回覆雜湊向量、分段串流、錯誤版本、非數值、過期 frame、逾時、停車接入
條件、失敗鎖存與原生模型復原。IPC 串流使用 socketpair，沒有實際 USB 裝置。

未完成：完整 AGNOS 編譯、原生 UI 畫面檢查、QCOM warp 執行及序列化、C4 USB
枚舉與斷線復原、Mac ANE/GPU 真實推論、20 Hz 全程延遲／丟幀、控制與道路測試。
請先在停車與未啟用控制的環境收集紀錄，完成這些驗證後才評估後續測試。

## 回復

關閉 JetlinkEnabled，重新啟動，或切回原本已驗證的分支。這個分支不改寫 Mac
檔案、韌體或車輛 CAN safety 設定。不要在行駛中切換軟體分支。

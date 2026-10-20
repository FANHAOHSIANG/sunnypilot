# sunnypilot + Mac Jetlink 實驗分支

本分支以 TonyBinheWu/sunnypilot `master` 的
`c6f579b270ec04cb2f50901cd5d7187614af41c3` 為基底。
Jetlink runtime 取自 ajouatom/openpilot carrot-wip
`ba186915179816a9b0476cec52bf321e73d2d4b0`；原有 MIT 授權保留於
`third_party/jetlink/LICENSE`，上游版本紀錄見 `UPSTREAM.md`。

**狀態：程式整合完成，尚未完成 C4 / Mac 實機驗證，不能視為已可上路的版本。**

## App 模型選擇支援範圍

- C4 保留 sunnypilot 預設（stock）模型執行器，Mac 使用 protocol 3。
- Mac Jetlink App 選擇並準備好模型後，C4 讀取 App 的 loaded SHA256，
  驗證完整 spec 後接入，不再只鎖 Cinque v2。
- 支援相容的 queued 與 stateful 匯出；固定相機布局為 2 × 6 × 128 × 256，
  frame_skip 為 4，traffic_convention / action_t 各為 1 × 2。
- queued 模型需提供 desire_pulse、features_buffer 與匹配的 hidden_state；
  stateful 模型需提供 new_img、desire 與匹配的 state_* / next_state_*。
- 可解析直接 plan 與舊式五候選 plan。其他必要輸出寬度、切片、範圍與 IPC
  大小均會檢查；未知格式顯示錯誤並保留本地模型，不會直接放行。
- 每個連線根據實際 spec 配置緩衝區，重新接入時重設歷史，不沿用上一個模型狀態。
- USB 3 SuperSpeed 實體資料線；沒有網路或手機網路設定。
- Jetlink 開關預設關閉；sunnypilot 的控制器、RELC、DesireHelper、車輛介面保留。

**格式檢查通過不等於已驗證模型的行車表現。** 現在沒有逐一實測 App 清單中的
每個大模型，因此不宣稱所有清單項目都相容。沒有需要的輸入／輸出、不同影像
尺寸或需要額外導航輸入的模型仍會拒絕接入。RL Nav 的導航、定位、路線跟隨與
HUD 也沒有因為開放模型格式而完成整合。

仍不支援：sunnypilot 自訂模型的 modeld_v2 / split / multi-policy 本地執行器、
Chestnut 與 Jetlink 同時使用、HUD／地圖影像通道。選到這些本地執行器時保留
原生模型，不會強制改選。

## 使用與換模型

1. 將 C4 更新為本分支，完成正常編譯／啟動；Small Model 選成 Default。
2. 在 Models 或 Toggles 開啟 **Jetlink Mac (Experimental)**，重新啟動。
3. 停車、解除控制，並實際關閉點火。always-offroad 介面覆寫不算完成連線準備。
4. Mac Jetlink App 選擇想使用的大模型，按 App 的 Use Model／準備模型操作，
   等待載入完成。本分支不負責在 Mac 下載、轉換模型。
5. 重新連接 USB 3，等待 C4 狀態變成 ready，再在停止且控制未啟用時接入。
6. Models 顯示 `Active — App model <SHA前12碼>` 才代表 modeld 使用該模型；
   `ready` 只代表 USB daemon 就緒。Cinque v2 保留原本的名稱。
7. 換下一個模型時，再次停車、解除控制、關閉點火，於 App 完成載入後重新連線。

App 必須支援 protocol 3 並在推論 telemetry 回報 loaded 模型身分。
這次修改不要求更換 Mac 的 HUD 程式；若 App 不提供上述資料，C4 會拒絕接入。

若行駛中直接在 App 換模型，不會即時套用新模型；C4 會中止舊連線並要求接手。
每幀回覆都會核對模型身分，模型已變更／身分缺失的回覆不會轉交 modeld。
需要回到停車、解除控制並關閉點火後重新連線。

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

2026-10-20 更新驗證：40 項通過；1 項 CPU warp 像素檢查因測試環境未初始化
pinned tinygrad submodule 而跳過。先前環境也未能執行此檢查（缺 clang）。
已驗證：動態模型契約、queued／stateful 緩衝區與真實 parser 輸出、舊式多候選
plan、選模過程點火／身分改變的拒絕、模型身分變更回覆不轉交、本地 daemon
與 Client 的 socketpair 往返，以及 protocol 3 上游
請求／回覆雜湊向量、分段串流、錯誤版本、非數值、過期 frame、逾時、停車接入
條件、失敗鎖存與原生模型復原。IPC 串流使用 socketpair，沒有實際 USB 裝置。

未完成：完整 AGNOS 編譯、原生 UI 畫面檢查、QCOM warp 執行及序列化、C4 USB
枚舉與斷線復原、Mac ANE/GPU 真實推論、20 Hz 全程延遲／丟幀、控制與道路測試。
請先在停車與未啟用控制的環境收集紀錄，完成這些驗證後才評估後續測試。

## 回復

關閉 JetlinkEnabled，重新啟動，或切回原本已驗證的分支。這個分支不改寫 Mac
檔案、韌體或車輛 CAN safety 設定。不要在行駛中切換軟體分支。

## 安裝停在 99% 的下載改善（2026-10-20）

此 Mac Jetlink 分支不支援實體 Chestnut 同時使用，因此新安裝會略過
`big_driving_supercombo.onnx`（765,950,064 bytes）的 LFS 下載。檔案留作
LFS 指標，不是可在 C4 執行的模型。原生小模型與駕駛監控模型仍完整下載，
供 C4 回退與監控使用；Mac App 的模型準備不受這個設定影響。

七個子模組改採 shallow clone，保留各自原本的固定 commit，減少歷史資料下載。

這項改善處理了不必要的下載量，不能單憑它就判定使用者 C4 停在 99% 的根因。
舊安裝器的進度只解析部分 Git 訊息，LFS 與子模組可能在同一個百分比等待，
也可能發生本機網路或 Git 錯誤；仍需本機紀錄才能確認個別裝置狀況。

驗證：以相同 `git clone --depth=1 --recurse-submodules` 流程對修改後版本
重新取得檔案，確認原生小模型及駕駛監控完整，Chestnut 大模型維持指標，
其餘 268 個 LFS 檔案 SHA256 及七個子模組 commit 均已核對。
LFS 實際下載總量為 120,268,487 bytes，略過的 Chestnut 權重為 765,950,064 bytes。未於 C4 完成安裝／編譯實測。

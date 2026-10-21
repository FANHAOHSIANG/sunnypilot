# Mac 上路故障：與 zoompilot 的來源比對

比對 zoompilot/zoompilot 204f529566660e83d25599e0f5870deaa5cc23ca 的
jetlink_repo/jetlink/openpilot/joining.py 與 sunnypilot/jetlink_adapter。
使用者回報 big model ready 後 Cruise Fault，以及重新啟動後 paramsd process not running；
尚未取得該次行車的 traceback / qlog，不能把所有故障認定為同一原因。

| 項目 | 原本本分支 | 本次修正與限制 |
| --- | --- | --- |
| ready | daemon 的模型、連線與暖機就緒 | 不代表 modeld 已採用遠端輸出；Active 才是實際使用 |
| 換入大模型 | 要求新鮮、停止、控制未啟用的訊息 | 另外要求 carControlSP.mads.enabled 為 false；與 zoompilot 一樣，暫停的 MADS 仍阻止切換。保留更嚴格的 standstill 條件 |
| 遠端推論失敗 | 鎖存 commIssue、重設本機循環狀態，失敗當幀回傳 None | 參考 zoompilot，在相同當幀嘗試本機推論，避免僅因遠端失敗就缺少該幀輸出；關閉 socket 失敗不阻止本機恢復 |
| 故障警示 | 要求停止、解除控制才能清除鎖存 | 保留；不假裝可繼續巡航，不隱藏 native model 錯誤，不停用任何 ACC / CAN / safety 警示 |
| 本機暖機 | 小模型先完成至少三幀才可接入遠端 | 保留；回退循環狀態重設後重新累計，移動或 MADS 啟用時不重新接入 |
| 遲到幀、證明期 | 與 zoompilot 不同 | 未移植 zoompilot 的 held frames、proving window、lag/drop 過濾重設與完整重試策略；不宣稱延遲或行車行為等同 zoompilot |

## paramsd 的另一個版本問題

官方 0.11.3 的 CarKalman 使用已除以車重的動力學係數。
保留的 HKG opendbc 1c51731 只有 mass、rotationalInertia、tireStiffnessFront、tireStiffnessRear，
沒有 rotationalInertiaPerMass / tireStiffnessFrontPerMass / tireStiffnessRearPerMass。
原本 paramsd 直接讀取新欄位，會在這份資料庫的 CarParams 上失敗。

新增 normalized_dynamics：舊欄位除以有效且正的 mass；新欄位直接使用，避免除兩次。
拒絕未知 schema、非有限或非正的係數，不用任意常數掩蓋資料問題，不改車輛資料庫或 safety。
單元測試使用真實 pinned HKG Cap'n Proto reader，並執行實際 paramsd 建構函式搭配記錄用 Kalman stub；
這不等同完成原生 Kalman library 的實機啟動／行車驗證。

## Cruise Fault 還需要紀錄

本分支的 Cruise Fault 對應 accFaulted；HKG CAN-FD 的來源是 TCS.ACCEnable != 0。
Jetlink fault 另由 commIssue 警示。訊息名稱與時序不足以判定 ACC 故障由模型切換造成。
沒有更動 accFaulted 的來源或警示，仍需該次事件附近的車端紀錄排查。

先保持停車，在 C4 取得啟動與模型錯誤：

    tmux capture-pane -p -J -S -10000 | grep -i -B 8 -A 20 -E 'Traceback|paramsd|Jetlink failed|accFaulted|cruiseMismatch|commIssue' | tail -n 240

不要為了產生日誌而在道路重現故障。重新啟動後舊 tmux 紀錄可能已消失，需另外保留行車 qlog。

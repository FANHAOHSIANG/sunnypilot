# Mac 上路故障：與 zoompilot 的來源比對

比對 zoompilot/zoompilot 204f529566660e83d25599e0f5870deaa5cc23ca 的
jetlink_repo/jetlink/openpilot/joining.py 與 sunnypilot/jetlink_adapter。
使用者回報 big model ready 後 Cruise Fault，以及重新啟動後 paramsd process not running；
尚未取得該次行車的 traceback / qlog，不能把所有故障認定為同一原因。

| 項目 | 原本本分支 | 本次修正與限制 |
| --- | --- | --- |
| ready | daemon 的模型、連線與暖機就緒 | 不代表 modeld 已採用遠端輸出；Active 才是實際使用 |
| 換入大模型 | 需要停止且控制未啟用 | 現在要求新鮮有效的 carState/selfdriveState/carControl/carControlSP，巡航與 MADS 全部關閉；可在移動中重接已準備的 warp。首次 GPU warp 準備仍只在停車時執行 |
| 遠端推論失敗 | 失敗當幀回傳 None | 同幀執行暖機過的本機模型；不隱藏本機推論錯誤 |
| 短暫延遲 | 同步等待至 IPC 逾時 | 一次最多一個背景 IPC；正常幀總預算 46 ms，遲到時有限度沿用上一份輸出，不排隊累積相機幀。第一次無輸出可保留時維持原有 150 ms 上限 |
| 延遲上限 | 無保留幀規則 | 連續 5 次或 10 秒內超過 20 次保留、輸出來源超過 200 ms、相機 frame_drop_ratio > 0.0075 時回退。不重用非有限或身分不符的輸出 |
| 切入驗證 | 無 | 前 20 幀為 proving；前三幀容許暖機，其後證明期遇到保留幀即回退。bigModelLoading 的 NO_ENTRY 同時阻止巡航及 MADS 啟用；控制狀態意外改變也立即回退 |
| 重試 | 固定 5 秒 | 前三次失敗各等 1 秒，之後 5/10/20/40/60 秒，上限 60 秒；成功持續超過 60 秒重設失敗次數。時間到仍須控制全關及本機完成三幀暖機 |
| 故障警示 | 停車解除控制才清除 | 保留 commIssue 退出控制；本機三幀暖機完成且控制全關後才解除鎖存。證明期不清除鎖存，不停用 ACC/CAN/safety 警示 |

來源：zoompilot/jetlink d24328d849b0e5154b55f149dc18c5dfe8bd79cc 的
joining.py / model_state.py（MIT），以及 zoompilot/zoompilot 204f5295 的 Adapter.in_control。
這是符合本分支 IPC 與 stock modeld 的移植，不是直接替換整套 zoompilot adapter。
同一 USB 引擎仍存活時，推論延遲回退可在本次行程重試；USB 整個斷線、
App 換模型或 USB 管理程序重建連線仍要求實際關閉點火，未放寬 USB 初始化限制。
只在實際切換當幀重設掉幀濾波，不隱藏後續持續落後。
軟體測試不代表已完成 C4/QCOM、Mac、iPhone/iPad 或實車驗證；不宣稱無縫維持巡航。

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

## C4 啟用控制後 UI 退出（完整 swaglog 確認）

0b0fc6c4d 的 swaglog.0000000234、237、247 各記錄一次相同的 UI crash：
C4 ModelRenderer._render 傳入 self._map_to_screen 給停車標記，但合併後的
C4 renderer 缺少該方法。這一行只在狀態不是 DISENGAGED 時執行；即使
停車標記選項關閉，Python 仍先解析 callback，因此依然崩潰。
這是直接的介面錯誤，不能把這三次 UI 退出解釋成 Jetlink 推論掉幀。

補回 C4 投影 callback，使用現有 _car_space_transform。C4 的點為視窗內
相對座標，繪製藍色路徑與停車標記時才加視窗原點；舊介面的絕對座標
呼叫維持預設行為。沒有改動車輛控制、Panda safety 或 Jetlink 回退條件。

六項無圖形視窗的回歸測試執行實際 _render/_draw_path 方法，覆蓋啟用控制、
MADS、解除控制、無效投影與兩種座標的停車線一致性；另保留 13 項藍色
路徑測試。這些測試隔離 raylib 初始化，不能取代 C4 真實畫面驗證。

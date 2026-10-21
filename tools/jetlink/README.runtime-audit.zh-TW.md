# Mac 上路故障：與 zoompilot 的來源比對

本次重新比對 zoompilot/zoompilot 632b0b0bebd608cb4846c00cec8c9522eac514f2 的
Adapter，以及該版實際釘選的 jetlink_repo b1d704188758536e573108162d0f84c316b427d9
之 joining.py / events.py / owner.py；不是只比對 Jetlink main 分支。
使用者回報 big model ready 後 Cruise Fault，以及重新啟動後 paramsd process not running；
尚未取得該次行車的 traceback / qlog，不能把所有故障認定為同一原因。

| 項目 | 原本本分支 | 本次修正與限制 |
| --- | --- | --- |
| ready | daemon 的模型、連線與暖機就緒 | 不代表 modeld 已採用遠端輸出；Active 才是實際使用 |
| 換入大模型 | 需要停止且控制未啟用 | 要求新鮮有效的 carState/selfdriveState/carControl/carControlSP，巡航與 MADS 全部關閉；可在移動中重接已準備的 warp。GPU warp 在 modeld 即時迴圈開始前，同一執行緒完成編譯及三次暖機 |
| 遠端推論失敗 | 失敗當幀回傳 None | 同幀執行暖機過的本機模型；不隱藏本機推論錯誤 |
| 短暫延遲 | 同步等待至 IPC 逾時 | 一次最多一個背景 IPC；正常幀總預算 46 ms，遲到時有限度沿用上一份輸出，不排隊累積相機幀。第一次無輸出可保留時維持原有 150 ms 上限 |
| 延遲上限 | 無保留幀規則 | 連續 5 次或 10 秒內超過 20 次保留、輸出來源超過 200 ms、相機 frame_drop_ratio > 0.0075 時回退。不重用非有限或身分不符的輸出 |
| 切入驗證 | 無 | 前 20 幀為 proving；前三幀容許暖機，其後證明期遇到保留幀即回退。bigModelLoading 的 NO_ENTRY 同時阻止巡航及 MADS 啟用；控制狀態意外改變也立即回退 |
| 重試 | 固定 5 秒 | 前三次失敗各等 1 秒，之後 5/10/20/40/60 秒，上限 60 秒；成功持續超過 60 秒重設失敗次數。時間到仍須控制全關及本機完成三幀暖機 |
| 故障警示 | 任何遠端失敗都鎖存 commIssue | 小模型同幀成功接手時僅提示，不額外解除控制；只有本機恢復失敗才鎖存 fault。真正的 ACC/CAN/定位/程序故障依舊生效 |

來源：早期移植為 zoompilot/jetlink d24328d 的 joining.py / model_state.py（MIT）；
本次以開頭的實際釘選 b1d7041 複核準備順序與有時限交接邏輯。
這是符合本分支 IPC 與 stock modeld 的移植，不是直接替換整套 zoompilot adapter。
USB 引擎仍存活時可重試；整個 USB 斷線後也允許在行駛中背景重連，
前提是有效且新鮮的硬體與車況訊息。不修改 IsOffroad 或線束點火訊號。
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
只有本機恢復失敗才由 Jetlink fatal fault 觸發 commIssue；遠端失效且本機正常不額外退出控制。
訊息名稱與時序不足以判定 ACC 故障由模型切換造成。
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

## 背景連線與模型切入分離

使用者確認整車關閉並等待後仍有 ignitionLine=True，原本所有 USB 重連
只允許 ignition-off，導致搬回車上或 C4 重啟後無法恢復連線。

現在真實 ignition-off 的原條件仍可建立連線；onroad 時則要求 deviceState、
pandaStates 有效新鮮（1.5 秒內、非空 Panda），carState/selfdriveState/
carControl/carControlSP 有效新鮮（0.25 秒內），且車速為有限值。背景 USB
連線與 App 引擎準備不要求 P 檔、零車速或控制關閉，因為不會發布駕駛輸出。
狀態資料失效時中止握手與暖機，不假造熄火或繞過 USB cleanup 的阻擋。

modeld 的 may_join 與 20 幀 proving 完全保留：巡航或 MADS 啟用時不得
換入大模型。大模型已運行後可以繼續巡航，點火訊號不會主動斷開它。
QCOM warp 不再於行車推論旁啟動另一個 GPU 編譯程序，而是在 modeld 啟動、
設定即時優先權及進入相機迴圈前準備。不依賴手機先連好，準備失敗仍使用
本機模型並記錄 prepare_failed，該次行車不再嘗試編譯；需重新啟動才能重試。
初次編譯可能增加 modeld 啟動時間，尚未量測 C4 QCOM 實機耗時。
重新連線不會自動替駕駛重新啟用巡航。

## 遠端失效時不中斷接手的程式路徑

修正先前移植與 zoompilot 的差異：遠端逾時、掉幀過多、壞輸出或斷線後，
先關閉遠端 client、重設本機循環狀態，在同一幀執行已暖機的小模型。
只有得到非空且所有值為有限數值的 native output，才發布 local/fallback 狀態
並加入 jetlinkFallback 提示；成功前不寫入 synthetic commIssue latch。
正常接手不要求先解除巡航或 MADS，也不會替駕駛開關巡航。

小模型重設失敗、推論例外、空輸出或非有限輸出仍寫入 fatal fault 並拋出；
既有程序存活、訊息時效與頻率、ACC、CAN、Panda、安全及定位檢查沒有移除。
本次新增最多一秒的交接緩衝，僅在已取得有效模型輸出的真實切換後生效，
時間從 handover_at 計算，不因狀態更新延長。交接前姿態與定位參數須健康；
交接中訊息存活、有效性、非模型服務檢查及 modelV2 200 ms 新鮮度仍須通過。
只容許模型衍生服務頻率、posenetOK 與 modeldLagging 的交接短暫波動。
inputsOK=False、vehicleParameters.valid=False、程序退出、CAN/ACC/Panda 和
本機 fatal fault 不受此緩衝豁免；持續姿態／頻率異常超過一秒立即回到原檢查。
這比 zoompilot 對整個通訊／定位區塊的 settling 處理嚴格，並非完全相同的 adapter。

依 zoompilot 的 handed_over 重啟現有 10 幀 camera-drop warm-up，僅容忍
實際模型交接造成的相機幀落後；服務存活、頻率與定位檢查仍保留。
V3 換回小模型時同步調整當幀的 lateral action delay，避免沿用 V3 的 0.1 秒平滑量。
恢復的大模型仍由原有 controls-off gate 與 20 幀 proving 驗證，兩個方向的限制不同。

回歸測試執行正式巡航與 MADS state-machine 方法，確認提示不解除控制，
並確認 accFaulted/canError/commIssue/locationdTemporaryError/processNotRunning
同時存在時仍觸發原有退出機制。另驗證 Cap'n Proto 新事件序列化、同幀輸出、
空／壞本機輸出、重設例外、後續本機崩潰、提示時效及 V3 時間參數。
沒有 C4/QCOM 或實車故障注入驗證，不建議在道路上刻意拔線測試。

## 連線與可觀測性

iOS 持續保留 NCM gadget，session 重試只關閉 TCP，避免 AGNOS 4.9 重建 NCM。
一般失敗仍等待兩秒；其間每 250 ms 觀察實體主機拔插，偵測重新接上即可提前重試。
Mac 仍使用本分支既有 SafeTransport cleanup，尚未換成 zoompilot 跨程序 Owner lease。
iOS 每幀模型身分缺省時仍保留 STATE 查詢，沒有為降低延遲移除身分驗證。

model status 新增 phase、handover_at、inference_ms。phase 區分準備中／失敗、
等待連線、等待解除巡航與 MADS、暖機、退避、連接及 proving。
inference_ms 是 modeld 的遠端路徑本幀處理時間（含 warp 與 IPC 等待），
沿用保留幀時不是新遠端結果的全程延遲，不能直接等同 App 引擎耗時。
daemon ready 但 model status 缺少或過期時顯示 live modeld status unavailable，
offroad 時 modeld 本來就不執行；ready 不是 active 的證據。

## HKG tinygrad 接手狀態重設修正

使用者紀錄中模型狀態停在開機後 63.859 秒，fatal fault 也在同一時間寫入，
fallback_count=0；程序清單沒有 driving modeld。這不是已完成的小模型接手。
該份篩選紀錄沒有最後 traceback，不能直接確認退出的唯一原因。

程式檢查確認另一個可重現的介面錯誤：釘選 tinygrad 69fd4d3 的 Buffer
沒有 copyin()，但遠端退出時 _reset_small() 仍使用它，會在歷史張量重設時
拋出 AttributeError。改為從零填充的 NPY Buffer 使用 copy_from()，保留原本
allocation 與 recurrent output/JIT 對狀態的 alias；不換張量、不忽略複製錯誤。

測試使用該釘選版真正的 Tensor 與 Buffer，覆蓋 state_pairs、舊式 _q、
非歷史輸入保持不變、同幀接手，以及複製失敗仍觸發 fatal fault。
新增 recovery_error 與 local_failed phase，區分遠端失敗和本機恢復失敗。
這些是 NPY/主機測試，不能代替 QCOM 直接記憶體存取與實車時序驗證。

## C4 Jetlink 圖示與完成通知

原本圖示只依實體 Chestnut 判斷，Big Model Ready 也只由 ChestnutLoading
下降觸發。Jetlink 使用 stock modeld，這兩條路徑沒有反映遠端模型狀態。
現在以 Jetlink 新鮮連線／模型紀錄，以及有效存活、200 ms 內的 modelV2.big
顯示狀態，不修改 chestnutPresent、ChestnutActive 或任何實體硬體參數。

C4 行車 HUD：連線就緒但未採用為白色；準備／proving 為白色閃動；
模型身分相符、active 且 proving 完成才顯示綠色；故障橙色。Jetlink 啟用時
行車圖示持續可見，不套用原生圖示數秒後淡出的規則；斷線仍不畫啟用圖示。
主頁保留原有連線圖示含義，已辨識 Jetlink 時不被 Chestnut 的 unknown USB
判斷隱藏；主頁連線就緒不代表模型正在駕駛。

Big Model Ready 僅在實際採用且完成 proving 後每次換入通知一次。狀態檔短暫
消失／過期不重複通知；daemon ready、offroad 暖機、失敗或模型仍在 proving
都不觸發。原有巡航、MADS gate 與推論切換邏輯沒有更動。

## 反覆 proving 失敗的排程與診斷

遠端 ready、fallback_count 持續增加且 recovery_error 為空，代表小模型正常
接手，但遠端無法通過證明期，不能解讀成大模型已穩定駕駛。
發現本分支 IPC worker／連接執行緒先前僅設 SCHED_OTHER，仍繼承 modeld
core 7 的 affinity。改用 zoompilot 相同的 transport.priority.background_thread，
同時降低優先權並移離單一即時核心；不在背景執行任何 GPU 工作。
另修正 Future 在等待逾時邊界剛完成時，回覆未經正常清除 pending／更新
output_at 的分支，避免重複取用同一回覆與錯誤來源年齡。

保留 46 ms、150 ms、200 ms、20 幀 proving、保留幀與掉幀界線。
和 zoompilot 一樣：即使 drain 收到上一請求的新回覆，本幀新請求未完成
仍算 held，證明期不因此視為通過。沒有提高逾時來掩蓋落後。
error 現在區分第幾個 proving frame、連續保留或十秒視窗累積保留。
模型紀錄新增 last_frame，保留失敗當幀的 warp_pack_ms、ipc_wait_ms、
total_ms、output_age_ms、held 與 drained_reply，回退後也不被成功小模型覆蓋。
warp_pack_ms 包含 drain／輸入打包及影像轉換；ipc_wait_ms 是本幀等待時間，
不是前一個請求完整往返時間。實際頻率與改善程度仍需 C4 紀錄確認。

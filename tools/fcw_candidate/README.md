# Ioniq 5 原廠 FCW 配套候選

**BENCH_ONLY — 尚未驗證可上路，沒有車上安裝／啟用步驟。**

這次是實際控制器 + Panda C 核心候選，不是另一支搜尋原始碼工具。固定 opendbc 基底：`1c51731784729da5ebaf24b3c34106bd27aaf94b`。原研究分支及其子模組指標不變。

## 修改方式

不直接放寬 raw forwarding。控制器只重送剛收到的原廠 Bus 2 `0x160` 完整 payload，不改狀態、未知欄位、CRC 或 counter；C 核心獨立驗證原始來源、CRC、連續 counter、頻率、100ms 新鮮度、逐 byte 一致及每筆只允許一次 TX。原本 `0x160` raw forwarding 仍隔離，避免雙重發送。

`0x1A0` 原廠 SCC 命令仍隔離；不新增 TCS 軟體 TX，不改 relay check、加速度或轉向限值。原廠 off／未知狀態仍原樣回報，不偽造 Available；未就緒或過期時候選撤銷 controls_allowed。

新測試 vehicle bit / safety bit 只適用固定版本，不能直接複製 Carrot 同號旗標。沒有 interface/UI 自動啟用。生產建置不帶 `HYUNDAI_FCW_BENCH` 時，請求新模式會禁止 TX/forwarding；define 僅在隔離 CI 的 host-side libsafety 測試複本加入。

## 本次已執行的本地結果

- Python 控制器 helper：15 項通過。
- 同一份新 C header：GCC + UBSan 20 項通過；Clang + UBSan 20 項通過。
- 是 35 個不同測試方法，不把換編譯器重跑算成新覆蓋。
- 含 128 個單 bit 損壞子案例、counter/timer wrap、重複、錯誤 bus、超時和完全相同 payload 的限制。
- 比對固定版本 checksum 函式及既有實車範例，16-byte `0x160` 使用 xor `0x041D`。沒有變更其他訊息 checksum。
- Python/shell 語法檢查通過。

這些是新 C 核心及 helper 的本地測試，**不是**整套修改後 libsafety 或 Panda MCU 韌體。先前使用者的 3,835 項測試是原版基準，不能挪作本候選通過結果。

## 完整整合測試

`prepare_candidate.py` 從固定 Git commit 匯出到全新隔離目錄，套入真實 controller、CarState、parser 及 Hyundai safety 修改並輸出 `FCW_CANDIDATE.patch`。不修改輸入 checkout，拒絕 comma 的 `/data/` 路徑。

`run_ci.sh` 執行完整原有 safety suite、生產模式拒絕新參數測試、bench 模式 Hyundai suite 與配套整合測試。工作流程已加入；必須有實際成功的 run 才能稱通過。任何一步失敗即停止，不發布車用韌體。

## 仍未驗證

完整修改後程式建置與回歸、controller replay、實際 Panda 收發時序、MCU 韌體、HIL、原廠 FCW 聲音/紅色顯示。0x160 不是已證實的完整 FCW 通道，AEB 制動仍未驗證。三筆有效來源、100ms 與5ms 間隔是候選工程假設，不是原廠規格。壞 frame 被既有 RX 層攔下時不會更新快取；先前尚未消耗的有效 frame 在新鮮度窗口內仍可能被授權，需在完整整合驗證。

不刻意在公路觸發 FCW/AEB。此候選不等於可安裝或可上路版本。

來源：https://github.com/TonyBinheWu/opendbc/tree/1c51731784729da5ebaf24b3c34106bd27aaf94b
安全要求：https://docs.comma.ai/concepts/safety/

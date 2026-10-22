# HKG + Jetlink 的 CommaView 相容安裝

此工具沿用官方 CommaView runtime `v0.0.59-beta`，搭配 Android App
`app-v0.0.163-beta`。官方目前不把 Tony 或 FANHAOHSIANG 的儲存庫列為支援來源；
這是本分支自行檢查的相容適配，不能當作 CommaView 官方認證。

## 已做的整合

- 從官方 HTTPS 來源下載 13 個安裝輔助檔，以 `release.json` 的 SHA256 核對。
- 只在下載的 apply/verify 兩個 shell helper 內，新增兩個明確的 sunnypilot 衍生來源：
  `FANHAOHSIANG/sunnypilot` 和 `TonyBinheWu/sunnypilot`。
- 保留真實 Git remote；不改官方執行檔、不處理登入、不改配對與模型，也不跳過停車檢查。
- 執行官方 UI transformer，在暫存副本上測試 C4 掛鉤與輸出 smoke check，再允許安裝。
- 安裝交給官方 installer：保留停車／解除控制檢查、runtime bundle 驗證、備份、回復與開機啟動。
- Jetlink 用 USB；CommaView 走 Wi-Fi。相容工具不改 USB gadget，也不替換 Jetlink。

## C4 安裝

先把 C4 更新到本儲存庫 `hkg-enhanced-jetlink`，正常開機。Android 與 C4 連同一個
Wi-Fi；車輛 P 檔且駕駛輔助解除，保持 C4 供電。在 SSH 連進 C4 後執行：

```sh
cd /data/openpilot
python3 tools/commaview/install.py --check
python3 tools/commaview/install.py --install
```

安裝完成依官方輸出取得 pair code。Android App 使用 **Manual setup → I ran the
command**，輸入 C4 IP 與一次性配對碼，再按 Open Live View。
若 App 沒有此手動配對流程，先確認安裝的是上述配對版本。

安裝器若判定正在行駛會停止；請停好後重新執行。此工具不強制切換 offroad。
若 UI 有其他本機修改，會在官方 installer 的 force-repair 前阻擋，避免覆蓋。

## 更新、卸載與限制

### 即時畫面有影像但沒有時速

先保持車輛停妥，開著 App 的即時畫面，在 C4 SSH 執行：

```sh
cd /data/openpilot
python3 tools/commaview/diagnose.py
```

工具只讀取原生 carState、HideVEgoUI、時速單位、UI 掛鉤及 CommaView
各遙測服務的設定與兩秒內送出增量，不輸出配對 token、VIN 或路線資料。
停車時 0 km/h 是有效數值；收不到資料與 0 不相同。

- 原生 carState 無效：先檢查車輛狀態來源。
- HideVEgoUI 開啟：確認隱藏時速是否為預期設定，不自動替使用者關閉。
- carState mode=off 或原生有效但 emittedCount 不增：檢查遙測設定與 App 連線。
- emittedCount 有增加仍不顯示：需 App 接收／解析紀錄與顯示設定，不能只重裝 UI 掛鉤。
- uiReloadPending：掛鉤檔已更新但執行中的 UI 尚未載入；停車後重新啟動。

已核對本工具固定的 v0.0.59-beta helper SHA256：它的 UI exporter 只提供
uiStateOnroad、carParams、projection 等 UI 資訊，並非舊公開 main 版的
逐服務 carState JSON exporter。因此沒有把舊 exporter 混入新 runtime，
也沒有宣稱此次已修好裝置上的時速問題；需要上述遙測才能定位。

- 不要用 CommaView 官方引導流程直接重裝此自訂分支，它的來源名單仍可能拒絕。
- App 內直接更新 runtime 可能覆蓋這兩個相容修正。更新應使用本分支的工具；
  本工具固定版本，不會自動跟進尚未驗證的新 runtime。
- 卸載使用官方提供的 `bash /data/commaview/uninstall.sh`，保持停車且解除控制。
- 尚未在 C4 執行 ARM64 runtime、Android 真實串流或 Jetlink 同時推論的負載測試。
  掛鉤與 smoke check 通過，只證明程式結構及資料輸出能匹配。

來源：<https://commaview.com/docs/install>、<https://commaview.com/docs/supported-forks>、
<https://commaview.com/api/current-release>。官方程式不在本分支重新散布。

# iPhone 15 Pro Max / iPad Air M4：Jetlink

兩台共用「iPhone / iPad」模式；一次只連一台。Mac 使用另一個模式。
這是本 sunnypilot 分支的軟體整合，尚未完成上述兩台實機與 C4 的 USB、模型推論或行車驗證。
iPad M4 或 iPhone 的晶片名稱本身不保證 V3 可持續 20 Hz。

## 手機／平板

1. 升級至 iOS / iPadOS 26.1 或以上。
2. 兩台各自開啟 https://testflight.apple.com/join/DAsYk5sP ，安裝官方 Jetlink beta。
3. 允許「本機網路」。先連網，在 App 選擇並下載、準備要用的相容模型。
4. 保持 App 在前景、裝置解鎖。切換 App／鎖定可能中斷連線。
5. 分別跑 1 分鐘及 10 分鐘 Benchmark；確認熱機後仍有時間餘裕。
   Benchmark 不含 C4、USB 傳輸、相機處理，通過仍不等於整體可用。

## C4

1. 停車、熄火、保持獨立供電。
2. 模型設定 → Select model → Small models → Default，不能保留 C210M / tinygrad runner。
3. 模型設定頁新增 Jetlink → iPhone / iPad。設定後重新啟動 C4。
   大介面的設定頁另有 Jetlink 開關和 iPhone / iPad mode 開關。
4. 使用 USB 3 資料線。若直連無法枚舉或供電不穩，使用有獨立供電的 USB 3 Hub：
   iPhone / iPad 為 Hub 主機，C4 的資料孔接 Hub 下游；C4 仍由原本電源孔供電。
   本分支不自動執行電源角色、充電電流或 USB PD 切換。
5. 一次接上其中一台，打開 Jetlink App，選好模型。
   C4 使用 USB NCM 網路讓 App 主動連入；不用手動填 Wi-Fi IP。
6. SSH 看：
   cat /dev/shm/sunnypilot-jetlink.json
   cat /dev/shm/jetlink-net
   ready 是模型與連線準備好；Active 才是 modeld 接入遠端結果。
   offroad 時沒有即時相機推論，不能僅因 ready 判定已行車驗證。

換 iPhone / iPad：保持熄火，拔掉前一台再接另一台；同模式不需改設定。
換回 Mac：Jetlink → Mac / Jetson，重新啟動 C4。
變更運算端或模型都必須在熄火時進行；在行駛中掉線仍依原有故障與本機模型處理。

## 實作與驗證範圍

- JetlinkIOS 為獨立的持久開關，預設 false；原本 Mac 的 USB bulk 模式不變。
- iOS 模式以 FunctionFS 持有 ep0／描述符，使複合 gadget 的 NCM 網卡枚舉，
  不向手機讀寫 vendor bulk endpoints。
- 沿用官方 USB NCM：C4 192.168.60.1/24，App 主動連 5599。
  只在 USB 網卡位址監聽，限制 peer 為該 USB 子網 .2 至 .254。
  沒有增加 UDP frame 路徑，先使用 TCP。
- 使用現有 protocol 3、SHA256 身分確認、ModelSpec 契約、熄火檢查與三幀暖機。
  不宣稱 App 的全部模型都符合本分支的輸出契約。
- 連線結束先關閉 phone socket，再釋放 ep0／gadget 和 DHCP；清理不完整時停止重試。
- ios-root.sh 原封不動取自 zoompilot/jetlink
  d24328d849b0e5154b55f149dc18c5dfe8bd79cc 的 jetlink/comma/jetlink-root.sh，
  保留 MIT 版權聲明。本 adapter 只呼叫 gadget --ios、net、teardown。
  該上游檔案包含其他子命令，本 adapter 不呼叫它們。
- 軟體測試包含 protocol 3 透過本地 socket 的 hello/state/二進位資料、
  USB 子網限制、點火改變／拔線／逾時清理、參數表和既有模型契約回歸。
  測試模擬 USB 位址，未使用真的 iPhone/iPad、USB NCM 或 CoreML。

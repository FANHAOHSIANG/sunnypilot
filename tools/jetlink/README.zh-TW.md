# hkg-enhanced + Jetlink (Mac / iPhone / iPad) / Cinque Terre V3

本分支 `hkg-enhanced-jetlink` 直接建立在 TonyBinheWu/sunnypilot **hkg-enhanced**
`d648262a015dfba1d8391d935c7fa93ea8214561`（2026-10-20）。
另已完整合併官方 sunnypilot master `922513f3128b00611d695ce465989a9bc8af9d59`，
目前 `COMMA_VERSION` 為 **0.11.3**。
Jetlink 原始移植取自 `jetlink-tony-sp-v3` `53b0fb2`。
本次官方合併保留 Tony 的 HKG torque 資料庫、導航、藍色路徑與停車標記。
沒有改寫車輛 CAN safety 設定。

**狀態：使用者已確認 C4 能進入介面，並提供 Panda SPI 成功連線紀錄；Jetlink USB、QCOM 遠端推論與行車尚未實機驗證。**

## iPhone / iPad

新增共用的 iPhone / iPad 模式，適用於 USB 3 的 iPhone 15 Pro Max 與 iPad Air M4。
詳見 [安裝與驗證步驟](README.iOS.zh-TW.md)。同時只連一台運算裝置；仍保留 Mac 模式。

## Panda 編譯配對修正

0.11.3 合併曾帶入 Panda `8a16e08`，其 `safety_tick()` 與 HKG safety 的
`void safety_tick(const safety_config *cfg)` 不相容。Panda 現固定回 HKG 原配套
`4230530f5ec4c7f11971f868ae9930b3547346eb`，保留 opendbc `1c51731`。
沒有刪除 safety 檢查、改動 safety header 或繞過韌體編譯。新增來源介面檢查與
push CI 防止再次配錯。此修正已核對宣告與呼叫；使用者後續已確認 C4 完成編譯、Panda SPI 連線並進入介面。

## 基底與依賴

- HKG opendbc：`1c51731784729da5ebaf24b3c34106bd27aaf94b`，來源 TonyBinheWu/opendbc。
- tinygrad 隨官方 0.11.3 更新為 `69fd4d34d4430036378aba2b43f9d339cdd6ea73`。
- opendbc 保留 HKG torque 修改；其他依賴依官方合併結果更新。
- Warp 讀寫同時辨識舊 HKG stream API 與新版 path/persistent-buffer API。
- 新增 BSM 相容層：新版 UI 在舊 HKG 車輛資料庫上沿用其 enableBsm 判斷；
  車輛資料庫提供新版 has_bsm 後直接使用它。
- 模型選擇器隨官方升至 selector 21、Chestnut catalog v28。

## 使用 Jetlink

1. 開啟 Jetlink 時自動使用 **內建 Small Model / stock runner** 作後備；保存原本本機模型選擇，關閉並重新啟動後恢復。
2. 開啟 **Jetlink → Mac / Jetson**，重新啟動 C4。
3. 建議出發前接好 USB 3；點火開啟或行駛中也可背景建立連線，不需要搬下 C4 或熄火。
4. Mac 的 protocol-3 Jetlink App 選模型並準備完成，C4 讀取實際 loaded SHA256。
5. 使用真實 ModelSpec 檢查輸入、輸出切片、大小與模型身分。ready 代表 daemon
   就緒；只有 **Active — 模型名稱** 才代表 modeld 正使用 Mac 結果。
6. 請在停妥後操作 App 換模型。模型身分改變會終止原推論；新模型連線就緒後，仍須巡航與 MADS 全關才可切入，通過 20 幀驗證後才允許啟用控制。

仍不支援：自訂本機 modeld_v2/split/multi-policy runner、Chestnut 與 Mac
同時使用、Jetlink HUD/地圖/導航影像通道。

支援符合契約的 queued/stateful 匯出；格式相容不等於每個模型已完成行車驗證。
目前不宣稱 App 清單上的每個模型都能用。遠端失敗會鎖存 fault，透過既有
commIssue 要求解除控制並回到本機模型；同一連線恢復後，巡航與 MADS 全關才允許重新接入，
不再要求車速為零。首次相機 warp 準備仍需停車；USB 重連與引擎背景準備不再要求關閉點火。
背景 ready 不代表切入；已啟用巡航或 MADS 時不會自動把小模型換成大模型。
採用有上限的保留幀、20 幀切入驗證及重試退避，細節見 [執行期比較](README.runtime-audit.zh-TW.md)。

## Cinque Terre V3

- CTV3M ref：`bf3e3631b3f91d92a1020a5e0dd4298b93ff4244`。
- ONNX SHA256：`404a18cfd86d29637d20c697dfde245bb47c666ae016730ab674c65f4d1e1aa4`。
- ONNX 大小：766,354,845 bytes。歷史狀態在 Mac；C4 傳相機資料及 scalars。
- 採用 V3 官方 catalog 的 0.1 秒橫向平滑 override。
- fixture 及 registry 來源為 zoompilot/jetlink `d24328d849b0e5154b55f149dc18c5dfe8bd79cc`；
  fixture 不是本環境重新執行實際 V3 模型的結果。
- V3 要在 Mac App 選，不是下載 C4 Chestnut 的 GPU 編譯檔。Mac App 必須支援
  protocol 3 與每次 inference 的 loaded 身分回報。

## USB 連線與診斷

USB 供電角色與資料角色不相同。此分支先認 configured UDC 或 UFP/device data
role；不再只憑「Source attached」判斷。若 C4 是 DFP/host，狀態會提示改用
USB 3 hub 或 USB-A 轉接器配 A-to-C 資料線，讓 Mac 當 host。沒有自動改寫 USB
角色或 USB PD 設定。SuperSpeed 仍是接入條件。

在 C4 的 SSH 執行以下唯讀診斷，可取得軟體版本、Jetlink JSON、USB 角色及速度：

```sh
cd /data/openpilot
bash tools/jetlink/diagnose.sh
```

`/dev/shm/sunnypilot-jetlink.json` 是 daemon 狀態；
`/dev/shm/sunnypilot-jetlink-model.json` 的 active 才是模型實際採用狀態。
狀態時間戳是 C4 的 monotonic time；重啟後才有新的狀態檔。

## CommaView

已加入可選的相容安裝工具：`tools/commaview/install.py`。
它用官方 runtime 與畫面 exporter，只對本分支來源加入明確分類，不改真實 remote。
詳見 [CommaView 安裝說明](../commaview/README.zh-TW.md)。
CommaView 真實串流及與 Jetlink 同時運行仍待 C4 驗證。

## 跟隨 HKG 更新

`.github/workflows/hkg-jetlink-upstream-sync.yml` 每日 03:33 UTC（台灣 11:33）
檢查 Tony **hkg-enhanced**，合併至獨立 sync 分支，通過檢查後開 PR。
同時合併官方 master；依賴或功能衝突會停止並需要人工處理。
這只建立更新 PR，不會自動合併，也不會更新已安裝 C4。
排程需相同 workflow 在 default master；首個排程執行及 Actions 建 PR 權限待確認。

## 本次驗證與限制

- Jetlink 51 項通過，含 V3 daemon/client 往返、模型身分改變拒絕、停車門檻、
  故障回復、USB data-role 判斷及 HKG 真實 tinygrad serializer 的 bytes roundtrip。
- 1 項 CPU Warp 像素檢查因環境缺 clang 跳過；QCOM 執行／序列化仍未驗證。
- Registry 48 項離線測試通過。
- CommaView 13 個官方輔助檔 SHA256、來源分類正／反向檢查、C4 UI transformer
  與 exporter smoke check 通過；沒有在工作環境執行實際 ARM64 installer。
- HKG opendbc torque 與導航、藍色路徑、停車標記保留；藍色路徑及翻譯 16 項、
  真實 HKG CarParams 盲點相容性 2 項檢查通過。完整 HKG 車輛測試與 AGNOS
  編譯未完成。先前 99%／逗號畫面及實際 Jetlink 未連線仍需要裝置日誌。
- `.lfsconfig` 略過 Mac 模式不用的 Chestnut 大模型權重；保留本機小模型與監控。
  新 HKG 基底的完整 LFS clone 尚未測試；不能沿用舊基底下載總量當作本次數字。
- 原有 `c4-install` 入口仍指向舊 `jetlink-mac-experimental`，沒有更換。

本機檢查：

```sh
python -m unittest discover -s openpilot/selfdrive/modeld/jetlink/tests -v
PYTHONPATH=third_party/jetlink python -m pytest -q -c /dev/null third_party/jetlink/tests/test_registry.py
python tools/commaview/install.py --check
```

關閉 Jetlink 後重啟，或切回已驗證的原分支，可恢復本機模型；不要在行駛中更新。


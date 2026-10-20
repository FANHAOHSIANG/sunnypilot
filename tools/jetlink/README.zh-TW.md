# hkg-enhanced + Mac Jetlink / Cinque Terre V3

本分支 `hkg-enhanced-jetlink` 直接建立在 TonyBinheWu/sunnypilot **hkg-enhanced**
`d648262a015dfba1d8391d935c7fa93ea8214561`（2026-10-20）。
Jetlink 移植自已建立的 `jetlink-tony-sp-v3` `53b0fb2`，不是把整個 master
蓋到 HKG 上。Tony 的車輛修改、導航、UI 與固定依賴保留；本次沒有改車輛 CAN safety。

**狀態：軟體整合／檢查通過，未完成 C4 啟動、QCOM、USB 或實際模型推論驗證。**

## 基底與依賴

- HKG opendbc：`1c51731784729da5ebaf24b3c34106bd27aaf94b`，來源 TonyBinheWu/opendbc。
- HKG tinygrad：`fe5d3169ba4f41d0947ad174925f413cbea9d056`。
- 所有 gitlink 維持 HKG 原本的 commit；七個 submodule 只加 shallow clone 提示。
- 配合這個 tinygrad 的 stream/out-of-band pickle API 調整 Warp 讀寫；不能混用
  先前 master 基底所用的 path/persistent-buffer API。
- 模型選擇器及其 catalog 版本沿用 HKG，不強制改成本分支以外的 runtime。

## 使用 Jetlink

1. 在 C4 選 **預設 Small Model / stock runner**。
2. 開啟 **Jetlink Mac (experimental)**，重新啟動 C4。
3. 停車、解除控制、實際關閉點火，再建立 USB 3 連線。
4. Mac 的 protocol-3 Jetlink App 選模型並準備完成，C4 讀取實際 loaded SHA256。
5. 使用真實 ModelSpec 檢查輸入、輸出切片、大小與模型身分。ready 代表 daemon
   就緒；只有 **Active — 模型名稱** 才代表 modeld 正使用 Mac 結果。
6. 換模型時再次停車、解除控制、關閉點火，待 App 載入完成再重新連線。

仍不支援：自訂本機 modeld_v2/split/multi-policy runner、Chestnut 與 Mac
同時使用、Jetlink HUD/地圖/導航影像通道、iOS USB 網路。

支援符合契約的 queued/stateful 匯出；格式相容不等於每個模型已完成行車驗證。
目前不宣稱 App 清單上的每個模型都能用。遠端失敗會鎖存 fault，透過既有
commIssue 要求解除控制並回到本機模型；只有停車且控制未啟用才允許重新接入。

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
不另把官方 master 自動蓋入 HKG；官方整合由 Tony 的 HKG 基底帶入，保留其
車輛相容性與固定依賴。發生衝突或檢查失敗會停止，不更新已安裝 C4。
排程需相同 workflow 在 default master；首個排程執行及 Actions 建 PR 權限待確認。

## 本次驗證與限制

- Jetlink 51 項通過，含 V3 daemon/client 往返、模型身分改變拒絕、停車門檻、
  故障回復、USB data-role 判斷及 HKG 真實 tinygrad serializer 的 bytes roundtrip。
- 1 項 CPU Warp 像素檢查因環境缺 clang 跳過；QCOM 執行／序列化仍未驗證。
- Registry 48 項離線測試通過。
- CommaView 13 個官方輔助檔 SHA256、來源分類正／反向檢查、C4 UI transformer
  與 exporter smoke check 通過；沒有在工作環境執行實際 ARM64 installer。
- HKG 原有控制、車輛程式與 gitlink 比對保持原值；未在此環境重跑全部 HKG
  車輛測試或完整 AGNOS 編譯。先前 99%／逗號畫面問題仍需要裝置日誌才能判定。
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

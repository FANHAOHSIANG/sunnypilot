# C4 直接輸入安裝網址

```
https://raw.githubusercontent.com/FANHAOHSIANG/sunnypilot/c4-install/install
```

在 C4 初始設定的 Custom Software / 自訂軟體頁面輸入完整網址。
本網址回傳原生 ARM64 ELF 安裝器，安裝目標固定為：

- 儲存庫：FANHAOHSIANG/sunnypilot
- 分支：jetlink-mac-experimental

安裝器會取得該分支當下的版本；不用把網址換成 GitHub 專案頁，也不要用只含帳號／分支的 openpilot 縮寫格式。

安裝完成後，Small Model 選 Default，啟用 Jetlink Mac (Experimental) 並重新啟動。換大模型與使用方式見 tools/jetlink/README.zh-TW.md。

此交付分支僅保存安裝器；軟體本體與後續更新仍來自 jetlink-mac-experimental。

來源與驗證

使用 sunnypilot 安裝服務在 C4（mici）請求下回傳的原生安裝器，依 installer.cc 的預留字串格式，只修改 GIT_URL 與 BRANCH_STR。保留原欄位大小、問號分隔符號、ELF 程式碼與檔案布局。詳見 installer-manifest.json。

已驗證下載內容為 AArch64 ELF，並核對目標儲存庫、分支及 SHA256。這不代表已在 C4 實際完成安裝；Jetlink 分支仍未完成完整 AGNOS 編譯與 C4/Mac 實機驗證。

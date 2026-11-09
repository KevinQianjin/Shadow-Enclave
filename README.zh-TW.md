# Shadow Enclave V4

[English](README.md) · [简体中文](README.zh-CN.md) · **繁體中文**

<img src="shadow-enclave-mark.png" alt="Shadow Enclave 戴面具的芭蕾舞者標誌" width="128">

Shadow Enclave V4 是一款採用英文介面的 Windows 桌面應用程式，可加密文字，並將密文嵌入圖片、音訊或文字載體。選擇 **Portable** 可傳送到其他電腦，選擇 **This PC Only** 則綁定目前的 Windows 帳戶和 TPM。你可以輸出單一載體，也可以把密文拆成兩份配對載體。

## 在 Windows 上安裝

你需要 64 位元 Windows 11、可核准安裝服務的系統管理員，以及已為使用 V4 的帳戶設定好的 Windows Hello。**This PC Only** 還需要可用的 TPM 2.0。預先建置的安裝包含有執行環境，接收者無需安裝 Python。

1. 在儲存庫的 **Releases** 頁面開啟 **Assets**，下載 `ShadowEnclaveV4-Windows-x64.zip` 和 `ShadowEnclaveV4-Windows-x64.sha256`。如果還沒有安裝包，可以[從原始碼建置](#從原始碼建置)。
2. 解壓縮前，用下列指令比對 SHA-256。安裝程式也會檢查內層安裝包的雜湊值。安裝包尚未簽署，雜湊值可用於檢查檔案完整性，其可信度取決於取得來源。

   ```powershell
   Get-FileHash .\ShadowEnclaveV4-Windows-x64.zip -Algorithm SHA256
   Get-Content .\ShadowEnclaveV4-Windows-x64.sha256
   ```

3. 將**完整**安裝包解壓縮到一個資料夾，執行 `Install V4.cmd`，核准 Windows 系統管理員提示。指令碼會安裝桌面程式及其背景服務。
4. 執行 `Start V4.cmd`。首次啟動時，選擇 **Enroll Windows Hello**，使用預定的 Windows 帳戶完成系統提示。指紋、人臉或 PIN 由 Windows 依裝置原則處理。

## 加密、傳送和解密

1. 開啟 **Encrypt**，輸入明文，選擇載體格式和份數。標準載體包括 PNG、BMP、WAV、FLAC 和純文字封裝（Text Armor）。AI 文字偽裝可將短訊息嵌入一份或兩份自然風格的英文文字。
2. 選擇 **Portable** 或 **This PC Only**，設定獨立的高強度通關密語並妥善保存，然後匯出載體。
3. 分享 **Portable** 訊息時，請以附件傳送原始檔，並透過另一個管道告知對方通關密語。雙載體必須傳送兩份。圖片或音訊轉碼、通訊軟體壓縮及編輯編碼後的文字都可能破壞嵌入的資料。
4. 在 **Decrypt** 中開啟載體或兩份配對載體，輸入通關密語並透過 Windows Hello 授權。

| 模式 | 可解密的位置 | 所需條件 |
| --- | --- | --- |
| **Portable** | 可在另一台電腦解密 | 支援 V4 的解密程式、通關密語和全部原始載體。使用官方程式時，接收者使用自己的 Windows Hello 註冊。 |
| **This PC Only** | 原電腦上的原 Windows 帳戶 | 通關密語、全部載體、受保護的服務資料、有效的 V4 Hello 註冊和原 TPM 金鑰。 |

V4 支援 V4 載體。V1–V3 檔案請使用對應的舊版程式開啟。

## DeepSeek 與隱私

標準加密、解密和本機圖片、音訊算繪皆可離線使用。使用 **Camouflage Text**、**AI Cover Document**、**AI Image Styling** 或 **AI Audio Styling** 時，需要在 **Settings** 填入你自己的 DeepSeek API 金鑰。AI 功能會將生成提示詞及你主動填寫的風格說明傳送給 DeepSeek。載體明文和通關密語留在本機，圖片和音訊也在本機算繪。解密已經生成的偽裝文字只需要原始載體文字，無需向 DeepSeek 發送請求。Windows 服務使用 DPAPI 保護已儲存的金鑰。移除金鑰需要確認並透過 Windows Hello 驗證。

## 安全性與復原

- V4 使用 Argon2id（512 MiB、三輪）衍生金鑰，並以 AES-256-GCM 加密。本機綁定也需要受 TPM 保護的秘密。載體外觀可以掩飾密文，但掩飾能力有限，應與加密強度分開看待。
- 啟動需要 Windows Hello。每次解密及變更已儲存的設定都要重新驗證。背景服務為每次解密嘗試設定至少 1 秒延遲，同一 Windows 帳戶失敗三次後冷卻 30 秒。選用的打字節奏與替代文件屬於本機功能。經修改的用戶端可以模擬按鍵間隔。
- **Portable** 檔案的副本可能遭到離線破解嘗試。傳送者的 Windows Hello、打字檢查、重試限制和替代文件不會隨檔案傳送。請使用高強度通關密語。
- **Unenroll Windows Hello** 會撤銷 V4 工作階段，並保留服務資料和 TPM 金鑰。重新註冊成功後才能解密。清除 TPM、刪除服務資料、重裝 Windows 或重設 Hello 可能使 **This PC Only** 檔案無法復原。需要獨立復原時，請保留一份 **Portable** 副本。
- 設計的防護範圍是一般 Windows 使用者程序。系統管理員權限、已遭入侵的作業系統，以及在授權後讀取明文的惡意軟體都超出這個範圍。V4 尚未經過獨立安全稽核。

## 更新與疑難排解

- **更新程式**。關閉 V4，再從新解壓縮的安裝包執行 `Install V4.cmd`。修復安裝會使用原有的受保護資料夾並保留註冊。預設資料夾為 `%ProgramData%\ShadowEnclave\V4`。
- **Hello 或 TPM 無法使用**。先在 Windows 中設定 Hello。如需綁定本機，確認 TPM 2.0 可用，且 Windows 允許程式所需的 Hello 原則。傳送檔案時選用 **Portable**。
- **無法讀取載體**。使用原始附件，確認兩份載體來自同一次匯出，並核對 V4 格式與通關密語。
- **DeepSeek 請求失敗**。檢查金鑰、網路、API 餘額和服務狀態。標準離線載體仍可使用。

如果要自訂驗證資料的位置，首次安裝時可在 PowerShell 執行 `& '.\Install V4.cmd' -DataDirectory 'D:\ShadowEnclaveV4Data'`。請選擇本機磁碟上的空資料夾，路徑中應無符號連結或目錄接合。修復安裝會繼續使用此位置。

## 從原始碼建置

在 64 位元 Windows 上安裝包含 Tcl/Tk 的標準 CPython 3.14，並確認系統有 .NET Framework 4.x 編譯器。在儲存庫根目錄開啟 PowerShell，執行以下指令。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip --isolated install --only-binary=:all: --index-url https://pypi.org/simple -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest -q test_v4
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Build-V4.ps1 -Python .\.venv\Scripts\python.exe
```

建置後會產生 `release/ShadowEnclaveV4-Windows-x64.zip` 和配套的 `.sha256`。單元測試使用暫時的測試資料。硬體 Hello 和 TPM 測試需要可互動操作的 Windows 電腦。

維護者可以執行 **Actions → Build Windows installer → Run workflow**。工作流程會在 GitHub 託管的 Windows 環境中測試和建置，無需存取你電腦上的服務資料或金鑰。下載並解壓縮工作流程產物，再將安裝 ZIP 和 `.sha256` 加到 GitHub Release。Release 頁面上的 **Source code** 壓縮包包含儲存庫原始碼。

## 版權

Copyright © 2026 Shadow Enclave Project。請參閱 [COPYRIGHT.txt](COPYRIGHT.txt)。安裝包內的 `licenses/` 包含第三方授權聲明。

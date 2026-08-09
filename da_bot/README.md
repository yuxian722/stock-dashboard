# DA 設備監控機器人 — 測試套件

## 現況更新(CPIS改版)

`cpis_scraper.py` / `cpis_utilization_scraper.py` 已經改用 `cpis_api.py`
(純 `urllib.request` 發HTTP請求)取代Selenium附身模式，不再需要開附身模式Edge、
不再卡在除錯模式Edge開不起來的問題。使用前請複製 `config.txt.example` 改名為
`config.txt`，填入CPIS/APG帳密。詳見 `cpis_api.py` 開頭說明。

這次進版控只帶了正式運作用到的程式檔，`da_maintenance.db`(測試資料庫)、
`inspect_*.py`(手動掃欄位用的除錯工具)、`msedgedriver.exe`、
`edge_debug_profile/`(瀏覽器暫存)沒有一起搬進來，你本機那份資料夾繼續用即可。

## 資料夾內容

| 檔案 | 用途 | 現在能不能直接跑 |
|---|---|---|
| `query_bot.py` | 查詢邏輯(問機台代號回覆修機/改機統計+明細+超標警示) | ✅ 可以,已用真實資料測試過 |
| `da_maintenance.db` | 已匯入你給的 Excel 測試資料的 SQLite 資料庫 | ✅ 可直接用 |
| `inspect_page.py` | 自動掃描 CPIS 網頁欄位工具(取代手動F12) | ✅ 可以跑,幫你把欄位資訊列出來 |
| `cpis_scraper.py` | 正式抓取腳本骨架 | ⚠️ 還缺欄位id,要先跑完 inspect_page.py 拿到資訊給我補 |

## 第一步:先測試查詢邏輯(不需要公司網路,現在就能測)

```
pip install --break-system-packages -r requirements_query.txt   (只需要標準庫,通常不用裝東西)
python query_bot.py BA205
python query_bot.py BA205 detail
python query_bot.py BAA08
python query_bot.py BA257 detail      <- 這台會看到超標警示範例
```

你可以自己挑資料庫裡其他機台代號試試看,回覆格式滿不滿意直接跟我說,要調文字、加欄位都可以馬上改。

## 第二步(要在公司電腦、連得到CPIS的環境跑):掃描網頁欄位

```
pip install pywin32
python inspect_page.py login
```

會跳出瀏覽器開登入頁,你確認畫面沒問題後,回到終端機視窗按 Enter,
它就會把畫面上所有輸入框、下拉選單、按鈕的 id/name 存成 `page_fields_login.txt`。

同樣方式對 EE Maintenance Record 查詢頁跑一次:
```
python inspect_page.py ee
```
(這次要先手動登入,登入完切到查詢頁面再按 Enter)

## 第三步

把兩個 `page_fields_*.txt` 檔案內容貼給我,我就能把 `cpis_scraper.py`
裡的 `[TODO 待確認]` 全部改成正確的欄位名稱,讓它能真正自動登入+查詢+存進資料庫,
不用再靠你手動匯出 Excel。

## 已知限制(誠實說明)

- 超標警示目前只有 CED(2.3hr)、CE/CEE(3hr)兩組標準工時,其他 Job Code 沒有警示(不會報錯,只是沒顯示)
- 即時「進行中」狀態(改機中/修機中,像同事整點推播那種)還做不到,需要等 cpis_scraper.py 完成後,
  設計一個「持續輪詢未結束事件」的邏輯,是下一階段的事
- Team+ 推播/接收訊息這段,還沒開始做,建議你先問劉俊威他們「推TEAM+」按鈕背後怎麼接的

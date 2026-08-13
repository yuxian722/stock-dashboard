# da_bot 開發筆記

DA(Die Attach)部門CPIS資料爬蟲+team+機器人。這份文件記錄踩過的坑，
避免下次開發重複犯錯、繞遠路。

## CPIS爬蟲：先看資料，再懷疑機制

2026/08/13班別查詢功能debug花了非常多輪才找到根因，關鍵教訓：

**當自動化「看起來每一步都正確」卻查不到資料時，第一件事是把爬蟲實際
抓到的原始HTML存成檔案、用瀏覽器打開來看，不是繼續猜測「是不是登入
沒成功」「是不是欄位沒填對」「是不是需要多點一個按鈕」。**

這次的真正根因（`cpis_scraper.parse_ee_maintenance_shift_html()`）：
CPIS的ASP.NET GridView表頭是可排序按鈕(`<th><input type="submit"
value="MACHINE ID"/></th>`)，文字在`value`屬性裡、不是文字節點，
BeautifulSoup的`get_text()`永遠抓到空字串，白名單表頭比對永遠失敗，
整個表格被誤判成「不是資料表格」而跳過——但因為外層的頁面級別檢查
(`_find_result_html()`)是用原始HTML字串搜尋，不是用`get_text()`，兩邊
判斷依據不一致，導致「有找到頁面(不丟例外)、但解析出0筆」這種矛盾
現象，被誤以為是查詢本身沒抓到資料。

`cpis_pm_monitor_scraper.py`的`_cell_text()`早就踩過、修過同一個坑
（見該檔案內註解），但這次寫新的HTML表格解析器時忘記套用同樣的
防禦寫法。**以後只要是解析CPIS任何頁面的HTML `<table>`，表頭/儲存格
文字擷取一律要先試`get_text()`、抓不到內容再retry讀裡面`<input>`的
`value`屬性，不要只寫`get_text()`就假設一定拿得到文字。**

在花時間懷疑機制（登入、Operation複選框、blur事件、bot偵測）之前，
先確認：這一輪測試「有沒有抓到真正的資料」，而不是「程式有沒有丟
例外」——沒丟例外不代表資料是對的，可能只是解析邏輯默默跳過了正確的
表格。

## CPIS的兩種查詢入口，能力不同

- **report產生端點**（例如`maintenance_record_r.aspx`）：query string
  直接帶條件、GET後端自動產生報表(.xls)下載連結，純`urllib`就能做
  (`cpis_api.py`)，穩定、簡單，但只支援它明確列出來會處理的欄位——
  不要假設它「應該」支援某個看起來合理的參數，要實測驗證(用
  `_probe_shift_filter.py`這類對照工具，比較加了條件前後筆數有沒有
  變化，不要只看「有沒有回傳資料」)。
- **互動式查詢表單**（例如`maintenance_record_h.aspx`）：功能較完整
  (例如真正的Shift篩選)，但只能用真人操作的方式跑，純`urllib`模擬
  POST容易漏欄位、踩到ASP.NET postback的各種細節(EventValidation/
  DropDownCheckBoxes等)，遇到就直接改用Selenium無頭瀏覽器(比照
  `cpis_pm_monitor_scraper.py`的既有作法)，不要在純HTTP模擬上死磕。

修改任何「查詢參數名稱」之類的基礎共用函式（例如
`cpis_api._ee_query_string()`）之前，要記得這個函式可能被其他已經
穩定運作的功能共用——先在腦中(或直接翻程式碼)列出所有呼叫端，改完
一定要確認原本能用的功能沒有跟著壞掉，不要只驗證新功能。

## Selenium爬蟲除錯順序建議

1. 先確認登入/導覽有沒有到對的頁面(印`driver.current_url`/`driver.title`)。
2. 確認表單欄位真的填進去了(截圖，或印出填寫後的欄位值)。
3. **確認Fetch/送出後，抓到的原始HTML裡有沒有真資料**（存檔用瀏覽器
   開，比截圖更可靠、能看到完整內容不受畫面尺寸限制）。
4. 只有在第3步確認HTML本身就是空的（例如頁面顯示"No Data"字樣）時，
   才回頭懷疑查詢條件/表單互動邏輯有沒有問題。
5. 如果HTML裡明明有資料、但解析函式回傳空結果，問題在解析邏輯本身，
   不是爬蟲操作——先檢查表頭/儲存格文字擷取方式(見上面`get_text()`
   vs `<input value>`那個坑)。

跳著做（例如還沒確認第3步就開始瞎猜表單欄位問題）會浪費非常多輪
使用者實測的來回時間。

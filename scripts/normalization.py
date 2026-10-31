"""Text normalization shared by indexing and querying."""
from __future__ import annotations

import unicodedata


_PAIR_TEXT = """
萬万 與与 為为 這这 個个 們们 來来 時时 後后 會会 發发 現现 業业 務务
國国 產产 開开 關关 學学 應应 經经 對对 於于 說说 還还 並并 書书 記记
頁页 條条 約约 簽签 訂订 貨货 額额 價价 錢钱 銀银 網网 絡络 總总 數数
據据 圖图 檔档 類类 區区 塊块 標标 題题 節节 點点 線线 號号 碼码 資资
訊讯 電电 腦脑 機机 設设 備备 軟软 體体 統统 環环 聯联 連连 結结 儲储
讀读 寫写 輸输 檢检 驗验 測测 試试 錯错 誤误 確确 認认 實实 際际 間间
變变 轉转 換换 邊边 緣缘 寬宽 視视 覺觉 問问 創创 結结 預预 計计 劃划
執执 準准 規规 則则 庫库 權权 錄录 帳帐 戶户 證证 憑凭 審审 匯汇 撥拨
稅税 費费 單单 掃扫 擷撷 調调 縮缩 東东 亞亚 灣湾 陸陆 時时 紀纪 員员 團团
隊队 組组 織织 門门 長长 監监 銷销 績绩 潤润 負负 債债 財财 報报 計计
詢询 覆复 協协 組组 級级 異异 誌志 優优 歷历 習习 認认 擇择 螢萤 幕幕
鼠鼠 鍵键 盤盘 擊击 載载 傳传 讓让 從从 嗎吗 麼么 樣样 誰谁 處处 動动
車车 馬马 鳥鸟 魚鱼 龍龙 龜龟 齒齿 風风 雲云 難难 舊旧 壞坏 進进 雖虽
憶忆 識识 專专 質质 醫医 療疗 藥药 訴诉 訟讼 判判 決决 週周 歲岁 鐘钟
顯显 觀观 聽听 響响 聲声 貝贝 殼壳 臺台 萬万 億亿 圓圆 幣币 賬账 財财
華华 區区 鎮镇 縣县 鄉乡 莊庄 園园 醫医 師师 護护 衛卫 廳厅 宮宫 廟庙
戰战 爭争 軍军 隊队 將将 帥帅 勝胜 敗败 傷伤 亡亡 來来 訪访 賓宾 館馆
飯饭 廳厅 廚厨 燒烧 烤烤 飲饮 嚐尝 營营 養养 衛卫 檢检 場场 館馆 廣广
電电 梯梯 樓楼 層层 階阶 門门 窗窗 牆墙 頂顶 陽阳 臺台 濕湿 溫温 熱热
氣气 霧雾 颱台 風风 預预 測测 來来 臨临 終终 啟启 動动 暫暂 停停 續续
刪删 除除 插插 入入 編编 輯辑 複复 製制 貼贴 剪剪 選选 擇择 以以 為为
雖虽 然然 但但 並并 且且 或或 者者 因因 此此 所所 以以 如如 果果 則则
將将 會会 已已 經经 可可以以 不不 能能 沒没 有有 應应 該该 必必 須须
"""

TRADITIONAL_TO_SIMPLIFIED = {
    pair[0]: pair[1]
    for pair in _PAIR_TEXT.split()
    if len(pair) == 2 and pair[0] != pair[1]
}
TRANSLATION_TABLE = str.maketrans(TRADITIONAL_TO_SIMPLIFIED)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.translate(TRANSLATION_TABLE)

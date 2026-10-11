/**
 * Google Apps Script nhận dữ liệu từ landing page (events + waitlist) và ghi vào Google Sheet.
 *
 * Cách dùng:
 * 1. Tạo một Google Sheet mới, mở Tiện ích mở rộng → Apps Script, dán toàn bộ file này.
 * 2. Triển khai → Tùy chọn triển khai mới → Loại: Ứng dụng web
 *    - Thực thi với tư cách: Tôi
 *    - Ai có quyền truy cập: Bất kỳ ai
 * 3. Sao chép URL ứng dụng web, dán vào CONFIG.ENDPOINT trong landing/index.html.
 * 4. Sheet sẽ tự tạo 2 tab: "events" và "waitlist".
 *
 * Landing gửi POST với body JSON dạng text/plain (mode no-cors), nên không cần CORS.
 */
const SHEETS = {
  event:    ['receivedAt', 'sid', 'vid', 'variant', 'name', 't', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'ref', 'referrer', 'props'],
  waitlist: ['receivedAt', 'email', 'segment', 'wtp', 'question', 'trigger', 'variant', 'sid', 'vid', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'ref', 'referrer', 'demo', 'createdAt'],
};
const KNOWN = new Set(['type', 'sid', 'vid', 'variant', 'name', 't', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'ref', 'referrer', 'utm', 'email', 'segment', 'wtp', 'question', 'trigger', 'demo', 'createdAt']);

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents || '{}');
    const type = data.type === 'waitlist' ? 'waitlist' : 'event';
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sh = ss.getSheetByName(type);
    if (!sh) { sh = ss.insertSheet(type); sh.appendRow(SHEETS[type]); sh.setFrozenRows(1); }
    const utm = data.utm || {};
    const get = k => data[k] != null ? data[k] : (utm[k] != null ? utm[k] : '');
    const props = {};
    Object.keys(data).forEach(k => { if (!KNOWN.has(k)) props[k] = data[k]; });
    const row = SHEETS[type].map(col => {
      if (col === 'receivedAt') return new Date();
      if (col === 'props') return JSON.stringify(props);
      if (col === 'demo') return data.demo ? JSON.stringify(data.demo) : '';
      if (col === 'email') return String(get(col)).toLowerCase().slice(0, 200);
      return typeof get(col) === 'object' ? JSON.stringify(get(col)) : get(col);
    });
    sh.appendRow(row);
    return ContentService.createTextOutput('ok');
  } catch (err) {
    return ContentService.createTextOutput('error: ' + err);
  }
}

function doGet() { return ContentService.createTextOutput('landing collector up'); }

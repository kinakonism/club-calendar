/**
 * Club Calendar 追加リクエストの受付（Google Apps Script）
 *
 * できること: サイトのフォームから届いた内容をスプレッドシートに1行追記し、自分宛にメールで知らせる。
 * ここでは何も追加・反映しない（受け付けて記録するだけ）。
 *
 * 使い方:
 *   1. https://script.google.com で新しいプロジェクトを作り、このファイルの中身を貼り付ける
 *   2. 関数 setup を1回実行（スプレッドシートが作られる。初回は権限の許可を求められる）
 *   3. デプロイ → 新しいデプロイ → 種類「ウェブアプリ」
 *        実行ユーザー: 自分 / アクセスできるユーザー: 全員
 *   4. 表示されたウェブアプリURL（…/exec）を index.html の REQUEST_ENDPOINT に入れる
 */

const SHEET_NAME = 'requests';
const DAILY_CAP = 30;       // 1日に受け付ける上限（いたずら対策）
const MAX_TEXT = 1000;

function setup() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('SHEET_ID')) {
    Logger.log('already set up: ' + SpreadsheetApp.openById(props.getProperty('SHEET_ID')).getUrl());
    return;
  }
  const ss = SpreadsheetApp.create('Club Calendar 追加リクエスト');
  const sheet = ss.getSheets()[0];
  sheet.setName(SHEET_NAME);
  sheet.appendRow(['受付日時', '種類', '内容', '名前', '状態']);
  sheet.setFrozenRows(1);
  props.setProperty('SHEET_ID', ss.getId());
  Logger.log('created: ' + ss.getUrl());
}

// 先頭が = + - @ の文字列は、シート上で数式として解釈されないようにする
function safeCell_(v) {
  const s = String(v == null ? '' : v);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// ---- お気に入りの同期（合言葉のハッシュをキーに、好みのJSONを1行で持つ）----
const PREFS_SHEET = 'prefs';
const PREFS_MAX_KEYS = 300;

function cleanPrefs_(d) {
  const out = {};
  ['artists', 'keywords', 'genres'].forEach(function (k) {
    out[k] = (Array.isArray(d && d[k]) ? d[k] : []).slice(0, 500).map(function (x) { return String(x).slice(0, 100); });
  });
  return out;
}

function prefs_(d) {
  const key = String(d.key || '');
  if (!/^[0-9a-f]{64}$/.test(key)) return json_({ ok: false, error: 'key' });
  const sheetId = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  if (!sheetId) return json_({ ok: false, error: 'setup' });
  const lock = LockService.getScriptLock();
  lock.waitLock(8000);
  try {
    const ss = SpreadsheetApp.openById(sheetId);
    let sheet = ss.getSheetByName(PREFS_SHEET);
    if (!sheet) {
      sheet = ss.insertSheet(PREFS_SHEET);
      sheet.appendRow(['key', 'data', 't', 'updated']);
      sheet.setFrozenRows(1);
    }
    const n = sheet.getLastRow() - 1;
    const keys = n > 0 ? sheet.getRange(2, 1, n, 1).getValues().map(function (r) { return r[0]; }) : [];
    const i = keys.indexOf(key);
    if (d.action === 'prefs_get') {
      if (i < 0) return json_({ ok: true, found: false });
      const row = sheet.getRange(i + 2, 2, 1, 2).getValues()[0];
      return json_({ ok: true, found: true, data: cleanPrefs_(JSON.parse(row[0] || '{}')), t: Number(row[1]) || 0 });
    }
    const t = Number(d.t) || Date.now();
    const data = JSON.stringify(cleanPrefs_(d.data));
    if (data.length > 40000) return json_({ ok: false, error: 'size' });
    if (i < 0) {
      if (n >= PREFS_MAX_KEYS) return json_({ ok: false, error: 'full' });
      sheet.appendRow([key, data, t, new Date()]);
    } else {
      sheet.getRange(i + 2, 2, 1, 3).setValues([[data, t, new Date()]]);
    }
    return json_({ ok: true, t: t });
  } finally {
    lock.releaseLock();
  }
}

function doPost(e) {
  try {
    const d = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (d.action === 'prefs_get' || d.action === 'prefs_set') return prefs_(d);
    if (d.website) return json_({ ok: true });  // 人には見えない欄が埋まっていたら bot とみなして黙って捨てる
    const kind = String(d.kind || '').slice(0, 20);
    const text = String(d.text || '').trim().slice(0, MAX_TEXT);
    const name = String(d.name || '').trim().slice(0, 40);
    const id = String(d.id || '').replace(/[^A-Za-z0-9-]/g, '').slice(0, 64);  // 再送時の二重登録を防ぐための送信ID
    if (!text) return json_({ ok: false, error: 'empty' });

    const props = PropertiesService.getScriptProperties();
    const sheetId = props.getProperty('SHEET_ID');
    if (!sheetId) return json_({ ok: false, error: 'setup' });

    const lock = LockService.getScriptLock();
    lock.waitLock(8000);
    try {
      const cache = CacheService.getScriptCache();
      if (id && cache.get('id_' + id)) return json_({ ok: true, dup: true });  // 応答が届かず再送されたもの
      const key = 'count_' + Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyyMMdd');
      const n = Number(props.getProperty(key) || 0);
      if (n >= DAILY_CAP) return json_({ ok: false, error: 'busy' });
      props.setProperty(key, String(n + 1));
      SpreadsheetApp.openById(sheetId).getSheetByName(SHEET_NAME)
        .appendRow([new Date(), safeCell_(kind), safeCell_(text), safeCell_(name), '未確認']);
      if (id) cache.put('id_' + id, '1', 21600);
    } finally {
      lock.releaseLock();
    }

    MailApp.sendEmail(
      Session.getEffectiveUser().getEmail(),
      '[Club Calendar] 追加リクエスト: ' + kind,
      '【Club Calendar 追加リクエスト】\n種類: ' + kind + '\n内容: ' + text + (name ? '\n名前: ' + name : '') +
      '\n\n※ まだ何も追加されていません。反映するかどうかを決めて Claude Code に伝えてください。'
    );
    return json_({ ok: true });
  } catch (err) {
    return json_({ ok: false, error: 'error' });
  }
}

function doGet() {
  return json_({ ok: true, service: 'club-calendar requests' });
}

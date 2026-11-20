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
  ['artists', 'keywords', 'genres', 'going', 'maybe'].forEach(function (k) {
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
      sheet.appendRow(['key', 'data', 't', 'updated', 'email', 'notified']);
      sheet.setFrozenRows(1);
    }
    const n = sheet.getLastRow() - 1;
    const keys = n > 0 ? sheet.getRange(2, 1, n, 1).getValues().map(function (r) { return r[0]; }) : [];
    const i = keys.indexOf(key);
    if (d.action === 'prefs_get') {
      if (i < 0) return json_({ ok: true, found: false });
      const row = sheet.getRange(i + 2, 2, 1, 4).getValues()[0];
      return json_({ ok: true, found: true, data: cleanPrefs_(JSON.parse(row[0] || '{}')), t: Number(row[1]) || 0, notify: !!row[3] });
    }
    if (d.action === 'notify_set') {   // 新着メールの宛先。空文字で停止。送るのは許可済みの宛先だけ
      if (i < 0) return json_({ ok: false, error: 'nokey' });
      const email = String(d.email || '').trim().toLowerCase().slice(0, 200);
      if (email && !/^[^\s@,;<>"]+@[^\s@,;<>"]+\.[a-z]{2,}$/.test(email)) return json_({ ok: false, error: 'email' });
      if (email && !notifyAllowed_(ss, email)) return json_({ ok: false, error: 'not_allowed' });
      sheet.getRange(i + 2, 5, 1, 2).setValues([[email, '']]);
      return json_({ ok: true, notify: !!email });
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

// ---- 新着メール ----
// 宛先にできるのは、このスクリプトの持ち主のアドレスと、シート「notify_allow」のA列に書いたアドレスだけ
// （誰でも好きな宛先を登録できると、迷惑メールの踏み台になるため）
const DATA_URL = 'https://kinakonism.github.io/club-calendar/data/events.json';
const SITE_URL = 'https://kinakonism.github.io/club-calendar/';

function notifyAllowed_(ss, email) {
  if (email === String(Session.getEffectiveUser().getEmail()).toLowerCase()) return true;
  let sh = ss.getSheetByName('notify_allow');
  if (!sh) { sh = ss.insertSheet('notify_allow'); sh.appendRow(['新着メールを送ってよいアドレス（1行に1つ）']); }
  const n = sh.getLastRow() - 1;
  if (n < 1) return false;
  return sh.getRange(2, 1, n, 1).getValues().some(function (r) { return String(r[0]).trim().toLowerCase() === email; });
}

// サイト側（index.html）と同じ照合ルール
function norm_(s) { return String(s || '').normalize('NFKC').toLowerCase().replace(/[’`]/g, "'").replace(/\s+/g, ' ').trim(); }
function stripName_(s) {
  return String(s || '').normalize('NFKC').replace(/(?<=.)[(\[【〈<][^)\]】〉>]*[)\]】〉>]/g, ' ')
    .replace(/\s+[-–—]\s*(live|dj|dj set|live set|hybrid set)\s*[-–—]?\s*$/i, ' ')
    .replace(/\s+(live set|dj set|hybrid set|live)\s*$/i, ' ').replace(/\s+/g, ' ').trim();
}
function core_(s) { const c = norm_(stripName_(s)); return c.length >= 2 ? c : norm_(s); }
function variants_(s) {
  const out = [norm_(s), core_(s)];
  String(s || '').split(/\s+(?:b2b|b3b|vs\.?)\s+/i).forEach(function (x) { if (x.trim()) out.push(core_(x)); });
  return out.filter(function (x) { return x; });
}
function wordRe_(s) {
  return new RegExp('(?<![a-z0-9])' + norm_(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![a-z0-9])');
}

function matchEvents_(data, prefs, today) {
  const follow = data.follow || {};
  const artists = (follow.artists || []).concat(prefs.artists || []).filter(function (x) { return norm_(x); })
    .map(function (x) { return [x, core_(x), wordRe_(stripName_(x) || x)]; });
  const rosters = (follow.labels || []).map(function (l) {
    const set = {};
    ((data.labels || {})[l] || []).forEach(function (a) { [norm_(a), core_(a)].forEach(function (n) { if (n.length >= 3) set[n] = true; }); });
    return [l, set];
  });
  const keywords = (prefs.keywords || []).filter(function (x) { return norm_(x); }).map(function (x) { return [x, wordRe_(x)]; });
  const out = [];
  (data.events || []).forEach(function (e) {
    if ((e.end_date || e.date) < today) return;
    const vars = (e.lineup || []).map(variants_);
    const names = {};
    vars.forEach(function (v) { v.forEach(function (x) { names[x] = true; }); });
    const hay = norm_([e.title, (e.lineup || []).join('\n'), e.text].join('\n'));
    const hits = [];
    artists.forEach(function (a) { if ((names[a[1]] || a[2].test(hay)) && hits.indexOf(a[0]) < 0) hits.push(a[0]); });
    rosters.forEach(function (r) {
      (e.lineup || []).forEach(function (a, i) {
        if (hits.length < 6 && vars[i].some(function (x) { return r[1][x]; }) && !artists.some(function (t) { return vars[i].indexOf(t[1]) >= 0; })) hits.push(r[0] + ': ' + a);
      });
    });
    keywords.forEach(function (k) { if (k[1].test(hay)) hits.push(k[0]); });
    if (hits.length) out.push({ e: e, hits: hits });
  });
  return out.sort(function (a, b) { return a.e.date < b.e.date ? -1 : a.e.date > b.e.date ? 1 : 0; });
}

function eventLine_(m) {
  const e = m.e, p = e.date.split('-');
  const dow = ['日', '月', '火', '水', '木', '金', '土'][new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2])).getDay()];
  return Number(p[1]) + '/' + Number(p[2]) + '(' + dow + ') ' + (e.venue_name || e.venue) + (e.open ? ' ' + e.open : '') +
    '\n  ' + e.title + '\n  一致: ' + m.hits.slice(0, 6).join(' / ') + (/^https?:\/\//.test(e.url || '') ? '\n  ' + e.url : '');
}

// 毎朝のトリガーで動く。お気に入りに一致する「まだ知らせていない」イベントをメールする
function dailyNotify() {
  const sheetId = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  if (!sheetId) return;
  const ss = SpreadsheetApp.openById(sheetId);
  const sheet = ss.getSheetByName(PREFS_SHEET);
  if (!sheet || sheet.getLastRow() < 2) return;
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 6).getValues();
  if (!rows.some(function (r) { return r[4]; })) return;
  const data = JSON.parse(UrlFetchApp.fetch(DATA_URL, { muteHttpExceptions: false }).getContentText());
  const today = Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy-MM-dd');
  rows.forEach(function (r, idx) {
    const email = String(r[4] || '').trim().toLowerCase();
    if (!email || !notifyAllowed_(ss, email)) return;
    try {
      const prefs = cleanPrefs_(JSON.parse(r[1] || '{}'));
      const matches = matchEvents_(data, prefs, today);
      const first = !r[5];
      const seen = {};
      (first ? [] : JSON.parse(r[5])).forEach(function (id) { seen[id] = true; });
      const fresh = matches.filter(function (m) { return !seen[m.e.id]; });
      if (fresh.length) {
        const shown = fresh.slice(0, 30);
        MailApp.sendEmail(email,
          '[Club Calendar] ' + (first ? 'お気に入りに一致するイベント ' : '新着 ') + fresh.length + '件',
          (first ? '新着メールの登録ありがとうございます。いま掲載されている、お気に入りに一致するイベントです。\n以降は新しく見つかった分だけお知らせします。\n\n'
                 : 'お気に入りに一致するイベントが新しく見つかりました。\n\n') +
          shown.map(eventLine_).join('\n\n') + (fresh.length > shown.length ? '\n\n…ほか ' + (fresh.length - shown.length) + '件' : '') +
          '\n\n' + SITE_URL + '\n（止めるときは、サイトの「好みを設定」→「端末間で同期」から）');
      }
      // 知らせた分として、今一致している今後のイベントのIDを覚える（空だと毎回「初回」になるので必ず何か入れる）
      sheet.getRange(idx + 2, 6).setValue(JSON.stringify(matches.map(function (m) { return m.e.id; }).slice(0, 1500)) );
    } catch (err) {
      console.error('notify failed for row ' + (idx + 2) + ': ' + err);
    }
  });
}

// 1回だけ実行する: 毎朝8時台（日本時間）に dailyNotify を動かすトリガーを作る
function setupNotify() {
  const exists = ScriptApp.getProjectTriggers().some(function (t) { return t.getHandlerFunction() === 'dailyNotify'; });
  if (!exists) ScriptApp.newTrigger('dailyNotify').timeBased().everyDays(1).atHour(8).inTimezone('Asia/Tokyo').create();
  UrlFetchApp.fetch(DATA_URL);   // 外部取得の権限もここで承認しておく
  Logger.log('notify trigger ready');
}

// clubberia 検索用: 同期されているお気に入りアーティストの合算（名前だけ。誰のものかは返さない）
function watchList_() {
  const sheetId = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  const sheet = sheetId && SpreadsheetApp.openById(sheetId).getSheetByName(PREFS_SHEET);
  if (!sheet || sheet.getLastRow() < 2) return [];
  const seen = {}, out = [];
  sheet.getRange(2, 2, sheet.getLastRow() - 1, 1).getValues().forEach(function (r) {
    try {
      cleanPrefs_(JSON.parse(r[0] || '{}')).artists.forEach(function (a) {
        const k = core_(a);
        if (k.length >= 2 && !seen[k] && out.length < 100) { seen[k] = true; out.push(stripName_(a) || a); }
      });
    } catch (err) { /* 壊れた行は飛ばす */ }
  });
  return out;
}

function doPost(e) {
  try {
    const d = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (d.action === 'prefs_get' || d.action === 'prefs_set' || d.action === 'notify_set') return prefs_(d);
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

function doGet(e) {
  if (e && e.parameter && e.parameter.list === 'watch') return json_({ ok: true, artists: watchList_() });
  return json_({ ok: true, service: 'club-calendar requests' });
}

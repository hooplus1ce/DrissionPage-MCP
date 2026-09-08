var all = document.querySelectorAll('*');
var result = { totalElements: all.length };
var classCount = {};
for (var i = 0; i < all.length; i++) {
  var cl = all[i].classList || [];
  for (var j = 0; j < cl.length; j++) {
    var c = cl[j];
    classCount[c] = (classCount[c] || 0) + 1;
  }
}
result.classTokenCount = Object.keys(classCount).length;

function pick(prefix) {
  var out = [];
  for (var k in classCount) {
    if (k.indexOf(prefix) === 0) out.push([k, classCount[k]]);
  }
  out.sort(function (a, b) { return b[1] - a[1]; });
  return out;
}
result.antClasses = pick('ant-').slice(0, 120);
result.legionsClasses = pick('legions-').slice(0, 60);
result.otherFramework = pick('rc-').slice(0, 20).concat(pick('spo').slice(0, 10), pick('wms').slice(0, 10), pick('scm').slice(0, 10));

var comps = {};
for (var k in classCount) {
  var m = k.match(/^ant-([a-z-]+?)(-[a-z]+)*$/);
  if (m) comps[m[1]] = 1;
}
result.antComponents = Object.keys(comps).sort();

var controls = [];
var fis = document.querySelectorAll('.ant-form-item');
for (var i = 0; i < fis.length; i++) {
  var fi = fis[i];
  var labelEl = fi.querySelector('.ant-form-item-label label');
  var control = fi.querySelector('.ant-select, .ant-input, .ant-picker, .ant-calendar-picker, .ant-input-number, textarea, input[type=radio], input[type=checkbox]');
  var kind = control ? (control.className.match(/ant-[a-z-]+/) || ['?'])[0] : 'other';
  var enabled = control ? (control.className.indexOf('disabled') < 0) : null;
  controls.push({ i: i, label: labelEl ? labelEl.textContent.trim() : null, kind: kind, enabled: enabled });
}
result.formItems = controls;

result.inputs = {
  text: document.querySelectorAll('input.ant-input, .ant-input input').length,
  select: document.querySelectorAll('.ant-select').length,
  selectEnabled: document.querySelectorAll('.ant-select.ant-select-enabled').length,
  picker: document.querySelectorAll('.ant-picker, .ant-calendar-picker').length,
  number: document.querySelectorAll('.ant-input-number').length,
  radio: document.querySelectorAll('.ant-radio').length,
  checkbox: document.querySelectorAll('.ant-checkbox').length,
  button: document.querySelectorAll('button').length
};

var bodyChildren = [];
var bc = document.body.children;
for (var i = 0; i < Math.min(bc.length, 40); i++) {
  bodyChildren.push({ tag: bc[i].tagName, cls: String(bc[i].className || '').slice(0, 60), id: bc[i].id || null });
}
result.bodyChildren = bodyChildren;

result.modals = [];
var ms = document.querySelectorAll('.ant-modal');
for (var i = 0; i < ms.length; i++) {
  var m = ms[i];
  var ti = m.querySelector('.ant-modal-title');
  result.modals.push({
    title: ti ? ti.textContent : null,
    visible: m.getBoundingClientRect().width > 0,
    hasFooter: !!m.querySelector('.ant-modal-footer')
  });
}

result.dropdowns = [];
var ds = document.querySelectorAll('.ant-select-dropdown');
for (var i = 0; i < ds.length; i++) {
  var d = ds[i];
  result.dropdowns.push({
    hidden: d.className.indexOf('hidden') >= 0,
    legacy: !!d.querySelector('.ant-select-dropdown-menu-item'),
    modern: !!d.querySelector('.ant-select-item-option'),
    optionCount: d.querySelectorAll('.ant-select-dropdown-menu-item, .ant-select-item-option').length
  });
}

result.libs = {
  scripts: (function () {
    var out = [];
    var ss = document.querySelectorAll('script[src]');
    for (var i = 0; i < Math.min(ss.length, 20); i++) out.push(ss[i].src);
    return out;
  })(),
  links: (function () {
    var out = [];
    var ls = document.querySelectorAll('link[href]');
    for (var i = 0; i < Math.min(ls.length, 20); i++) out.push(ls[i].href);
    return out;
  })(),
  antdVersion: (window.antd && window.antd.version) ? window.antd.version : null,
  reactVersion: window.React ? window.React.version : null
};

var vt = document.querySelectorAll('.vtable, canvas');
var at = document.querySelectorAll('.ant-table');
var lt = document.querySelectorAll('table');
var gridSet = {};
var gels = document.querySelectorAll('[class*="table"], [class*="grid"], [class*="vtable"]');
for (var i = 0; i < gels.length; i++) {
  var gl = gels[i].classList || [];
  for (var j = 0; j < gl.length; j++) {
    if (/table|grid|vtable/i.test(gl[j])) gridSet[gl[j]] = 1;
  }
}
result.tables = {
  vtableOrCanvas: vt.length,
  antTable: at.length,
  legacyTable: lt.length,
  gridClasses: Object.keys(gridSet).slice(0, 20)
};

return JSON.stringify(result);

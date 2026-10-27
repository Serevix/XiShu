/** 离线验证自定义报告渲染器的安全边界与数据保留，不连接浏览器或模型服务。 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

/** 提供仅含安全节点构建方法的最小 DOM，任何 innerHTML 使用都会立即报错。 */
class SafeNode {
  /** 保存节点种类与子节点，模拟文本和元素节点的必要行为。 */
  constructor(tag) { this.tag = tag; this.children = []; this.value = ''; }
  /** 追加子节点，字符串只作为普通文本保存。 */
  append(...nodes) { this.children.push(...nodes); }
  /** 读取所有后代的文字，用于检测报告中的数据是否被丢弃。 */
  get textContent() { return this.value + this.children.map(textOf).join(''); }
  /** 赋值纯文本不解析任何标签，与浏览器 textContent 的行为一致。 */
  set textContent(value) { this.value = String(value); this.children = []; }
  /** 禁止渲染器通过 HTML 字符串构造不可信节点。 */
  set innerHTML(value) { throw new Error('不允许使用 innerHTML'); }
}

/** 获取安全节点或普通字符串的可见文本。 */
function textOf(node) { return node instanceof SafeNode ? node.textContent : String(node); }

/** 构造普通元素，渲染器没有脚本执行或 HTML 解析能力。 */
function createElement(tag) { return new SafeNode(tag); }

/** 构造文本节点，保留尖括号和脚本文字。 */
function createTextNode(value) { const node = new SafeNode('#text'); node.value = value; return node; }

/** 展开后代节点，核实真实生成的表格和节点类型。 */
function descendants(node) {
  const result = [node]; for (const child of node.children) if (child instanceof SafeNode) result.push(...descendants(child)); return result;
}

/** 隔离加载前端代码，仅去掉启动入口，避免触发页面轮询和请求。 */
function renderer() {
  const source = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'),'utf8').replace(/\binitialize\(\);\s*$/, '');
  const context = vm.createContext({document:{createElement,createTextNode},sessionStorage:{getItem:emptyToken}});
  vm.runInContext(source,context);
  return context;
}

/** 离线测试没有认证令牌或用户会话。 */
function emptyToken() { return null; }

/** 将固定测试材料传入真实渲染函数，不调用任何模型或网络接口。 */
function render(context, source) { context.fixture = source; return vm.runInContext('renderMarkdown(fixture)',context); }

/** 验证 HTML、事件处理属性及外部链接都不会成为可执行的页面元素。 */
function untrustedTextRemainsInert() {
  const node = render(renderer(),'# 报告\n<img src=x onerror="alert(1)">\n<script>alert(2)</script>\n[链接](javascript:alert(3))\n**<svg onload=alert(4)>**');
  assert.ok(node.textContent.includes('<img src=x'));
  assert.ok(node.textContent.includes('javascript:alert(3)'));
  assert.ok(descendants(node).every(isSafeTag));
}

/** 排除任何能够执行脚本、加载远程资源或导航的元素。 */
function isSafeTag(node) { return !['img','script','svg','iframe','a'].includes(node.tag); }

/** 表格转义、金额、空值文字与代码原文必须保留，不以排版为由截断数据。 */
function tableAndCodeRemainComplete() {
  const source = '# 标题\n\n| 类别 | 金额 |\n| --- | --- |\n| a\\|b | 6550.00 |\n| 无定义/缺失 | -990.00 |\n\n```sql\nSELECT SUM(price) FROM order_items;\n```';
  const node = render(renderer(),source); const nodes = descendants(node);
  assert.equal(nodes.filter(isTable).length,1);
  assert.equal(nodes.filter(isCell).length,4);
  assert.ok(node.textContent.includes('a|b'));
  assert.ok(node.textContent.includes('-990.00'));
  assert.ok(node.textContent.includes('SELECT SUM(price) FROM order_items;'));
  assert.equal(nodes.filter(isDetails).length,1);
}

/** 识别生成的表格节点。 */
function isTable(node) { return node.tag === 'table'; }
/** 识别数据单元格而非表头。 */
function isCell(node) { return node.tag === 'td'; }
/** 识别按需展开的计算依据。 */
function isDetails(node) { return node.tag === 'details'; }

test('不可信 HTML 与链接只显示为文本',untrustedTextRemainsInert);
test('转义表格、金额及代码依据完整保留',tableAndCodeRemainComplete);

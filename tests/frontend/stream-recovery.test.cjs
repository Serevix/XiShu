/** 检查真实前端 SSE 代码的断线补读、去重和取消，不依赖浏览器或外部网络。 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

/** 在隔离运行环境加载前端，仅去掉页面初始化，保留实际的订阅逻辑。 */
function loadStream() {
  const source = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8').replace(/\binitialize\(\);\s*$/, '');
  const context = vm.createContext({sessionStorage:{getItem(){return null;}}, AbortController, TextDecoder});
  vm.runInContext(source, context);
  vm.runInContext('state.taskId="current"; state.taskStatus="running";', context);
  return context;
}

test('断线后按已读序号重连并去重，结束后停止订阅', async function recoverByCursor() {
  const context = loadStream(); const urls = []; const events = []; let cleared = 0;
  const encode = new TextEncoder();
  context.api = async function offlineApi(url) {
    urls.push(url); const first = urls.length === 1; let read = 0;
    return {body:{getReader(){return {
      async read() {
        read++;
        if (first && read === 2) throw new Error('connection closed');
        if (read > 1) return {done:true};
        const data = first ? 'data: {"seq":1,"type":"started","payload":{}}\n\n' : 'data: {"seq":1,"payload":{}}\n\ndata: {"seq":2,"type":"completed","payload":{}}\n\n';
        return {done:false,value:encode.encode(data)};
      },
      async cancel(){cleared++;}, releaseLock(){}
    };}}};
  };
  context.renderEvent = function collectEvent(event){events.push(event.seq);};
  context.toast = function silentToast(){};
  context.reconnectDelay = async function noDelay(){};
  context.reloadTaskResult = async function completedTask(){vm.runInContext('state.taskStatus="succeeded";',context);};
  await vm.runInContext('streamTask("current",0)',context);
  assert.equal(urls.length,2); assert.ok(urls[1].endsWith('after=1'));
  assert.deepEqual(events,[1,2]); assert.equal(cleared,2);
});

test('切换任务取消旧连接，退避后不重新订阅旧任务', async function abortOldSubscription() {
  const context = loadStream(); let requests = 0;
  context.api = async function brokenApi(){requests++; throw new Error('offline');};
  context.toast = function silentToast(){};
  context.reconnectDelay = async function changeTask(){vm.runInContext('state.stream.abort(); state.taskId="another";',context);};
  await vm.runInContext('streamTask("current",0)',context);
  assert.equal(requests,1);
});

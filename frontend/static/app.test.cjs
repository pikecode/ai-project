const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
    class Element {
        constructor() { this.children = []; this.textContent = ''; this.value = ''; this.files = []; }
        setAttribute(key, value) { this[key] = value; }
        showModal() { this.open = true; }
        close() { this.open = false; }
        appendChild(child) { this.children.push(child); }
        replaceChildren() { this.children = []; }
        addEventListener() {}
        set innerHTML(value) { throw new Error('Unexpected HTML rendering: ' + value); }
    }
    const elements = new Map();
    const document = {
        createElement: () => new Element(),
        getElementById: id => {
            if (!elements.has(id)) elements.set(id, new Element());
            return elements.get(id);
        }
    };
    class FormData { append() {} }
    const context = vm.createContext({document, TextDecoder, AbortController, FormData, console,
        fetch: async () => ({ok: true, json: async () => []})});
    vm.runInContext(fs.readFileSync(__dirname + '/app.js', 'utf8'), context);
    return {context, document, api: vm.runInContext('({readEvents, addMessage, updateMessage, send, openSource, previewKnowledge, importKnowledge, invalidateKnowledgePreview, addKnowledge})', context)};
}

function stream(text, bytewise = false) {
    const bytes = new TextEncoder().encode(text);
    return new ReadableStream({start(controller) {
        if (bytewise) for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
        else controller.enqueue(bytes);
        controller.close();
    }});
}

test('SSE handles Chinese UTF-8 and CRLF across individual network bytes', async () => {
    const {api} = setup();
    const events = [];
    await api.readEvents(stream('data: {"type":"message","content":"中文内容"}\r\n\r\ndata: {"type":"done"}\n\n', true), event => events.push(event));
    assert.equal(events.length, 2);
    assert.equal(events[0].content, '中文内容');
    assert.equal(events[1].type, 'done');
});

test('SSE rejects interrupted frames and reports server errors', async () => {
    const {api} = setup();
    await assert.rejects(api.readEvents(stream('data: {"type":"message"}'), () => {}), /中断/);
    await assert.rejects(api.readEvents(stream('data: {"type":"error"}\n\n'), () => {throw new Error('server failed');}), /server failed/);
});

test('chat content and updates are rendered as text', () => {
    const {api} = setup();
    const payload = '<img src=x onerror=alert(1)>';
    const element = api.addMessage(payload, 'assistant');
    assert.equal(element.textContent, payload);
    api.updateMessage(element, payload + '\n第二行');
    assert.equal(element.textContent, payload + '\n第二行');
});

test('send preserves attachment question and replaces final message instead of appending', async () => {
    const {api, context, document} = setup();
    vm.runInContext('selectedFileUrl = "uploads/test.pdf"', context);
    document.getElementById('message').value = '文件结论是什么？';
    let request;
    context.fetch = async (url, options) => {
        if (url === '/api/conversations') return {ok: true, json: async () => []};
        request = JSON.parse(options.body);
        return {ok: true, body: stream('data: {"type":"message","content":"回答"}\n\ndata: {"type":"done","response":"回答","conversation_id":"test"}\n\n', true)};
    };
    await api.send();
    assert.equal(request.message, '文件结论是什么？');
    assert.equal(request.attachment, 'uploads/test.pdf');
    assert.equal(document.getElementById('chat').children[1].textContent, '回答');
    assert.equal(document.getElementById('send').disabled, false);
});

test('failed request keeps input and retries the same user message', async () => {
    const {api, context, document} = setup();
    document.getElementById('message').value = '不要丢失的问题';
    let attempt = 0;
    const payloads = [];
    context.fetch = async (url, options) => {
        if (url === '/api/conversations') return {ok: true, json: async () => []};
        payloads.push(JSON.parse(options.body));
        attempt++;
        const data = attempt === 1 ? 'data: {"type":"thinking","conversation_id":"cid"}\n\ndata: {"type":"error","content":"暂时不可用"}\n\n' : 'data: {"type":"done","response":"恢复成功","conversation_id":"cid"}\n\n';
        return {ok: true, body: stream(data)};
    };
    await api.send();
    assert.equal(document.getElementById('message').value, '不要丢失的问题');
    assert.equal(document.getElementById('retry').hidden, false);
    await vm.runInContext('retryLast()', context);
    assert.equal(payloads[1].retry, true);
    assert.equal(document.getElementById('chat').children.length, 2);
    assert.equal(document.getElementById('chat').children[1].textContent, '恢复成功');
});

test('cancel aborts network wait and restores controls without losing the question', async () => {
    const {api, context, document} = setup();
    document.getElementById('message').value = '等待取消的问题';
    context.fetch = (url, options) => {
        if (url === '/api/conversations') return Promise.resolve({ok: true, json: async () => []});
        return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => {
            const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
        }));
    };
    const pending = api.send();
    vm.runInContext('cancelRequest()', context);
    await pending;
    assert.equal(document.getElementById('send').disabled, false);
    assert.equal(document.getElementById('cancel').hidden, true);
    assert.equal(document.getElementById('message').value, '等待取消的问题');
    assert.match(document.getElementById('task-status').textContent, /已取消/);
});

test('side effects disable one-click retry', async () => {
    const {api, context, document} = setup();
    document.getElementById('message').value = '发送操作';
    context.fetch = async (url) => {
        if (url === '/api/conversations') return {ok: true, json: async () => []};
        return {ok: true, body: stream('data: {"type":"thinking","conversation_id":"cid"}\n\ndata: {"type":"tool_call","tools":[{"tool":"send_email"}]}\n\ndata: {"type":"error","content":"结果未知"}\n\n')};
    };
    await api.send();
    assert.equal(document.getElementById('retry').hidden, true);
    assert.match(document.getElementById('context-warning').textContent, /确认结果/);
});

test('citation buttons show escaped original text and file provenance', async () => {
    const {api, context, document} = setup();
    const id = 'KB-1234567890abcdef';
    const element = api.addMessage('<img onerror=alert(1)> [' + id + ']', 'assistant');
    assert.match(element.children[0].textContent, /<img/);
    assert.equal(element.children[1].textContent, id);
    context.fetch = async url => {
        assert.equal(url, '/api/knowledge/sources/' + id);
        return {ok: true, json: async () => ({content: 'generated metadata header', original_content: '<script>not executable</script>', metadata: {filename: '制度.md', title: '制度', version: '1.2', part: 2, parts: 3}})};
    };
    await element.children[1].onclick();
    assert.equal(document.getElementById('source-content').textContent, '<script>not executable</script>');
    assert.match(document.getElementById('source-meta').textContent, /制度.md.*1.2.*2\/3/);
    assert.equal(document.getElementById('source-dialog').open, true);
});

test('source race cannot display the previous document and missing sources are explained', async () => {
    const {api, context, document} = setup();
    const pending = [];
    context.fetch = () => new Promise(resolve => pending.push(resolve));
    const first = api.openSource('KB-1111111111111111');
    const second = api.openSource('KB-2222222222222222');
    pending[1]({ok: true, json: async () => ({content: 'second', metadata: {title: '第二份'}})});
    await second;
    pending[0]({ok: true, json: async () => ({content: 'first', metadata: {title: '第一份'}})});
    await first;
    assert.equal(document.getElementById('source-content').textContent, 'second');
    context.fetch = async () => ({ok: false, json: async () => ({detail: '当前知识库没有此引用'})});
    await api.openSource('KB-aaaaaaaaaaaaaaaa');
    assert.match(document.getElementById('source-content').textContent, /没有此引用/);
});

function previewData() {
    return {payload: {expires_at: 123456, parts: [{content: '# 标题\n第一行\n第二行', metadata: {filename: '资料.md', version: '1.0'}}]}, signature: 'a'.repeat(64)};
}

test('file preview requires a separate confirmation and metadata changes invalidate it', async () => {
    const {api, context, document} = setup();
    document.getElementById('knowledge-file').files = [{name: '资料.md', size: 30}];
    const calls = [];
    context.fetch = async (url, options) => {
        calls.push(url);
        if (url.endsWith('/preview')) return {ok: true, json: async () => previewData()};
        if (url.endsWith('/import')) {
            assert.deepEqual(JSON.parse(options.body), previewData());
            return {ok: true, json: async () => ({added: 1})};
        }
        return {ok: true, json: async () => ({total_documents: 1})};
    };
    await api.previewKnowledge();
    assert.deepEqual(calls, ['/api/knowledge/preview']);
    assert.match(document.getElementById('knowledge-preview-text').textContent, /第一行\n第二行/);
    api.invalidateKnowledgePreview();
    await api.importKnowledge();
    assert.deepEqual(calls, ['/api/knowledge/preview']);
    await api.previewKnowledge();
    await api.importKnowledge();
    assert.equal(calls.filter(url => url.endsWith('/import')).length, 1);
    assert.equal(document.getElementById('knowledge-import-btn').disabled, true);
});

test('a stale preview never enables confirmation for a changed file', async () => {
    const {api, context, document} = setup();
    document.getElementById('knowledge-file').files = [{name: 'a.md', size: 20}];
    let complete;
    context.fetch = () => new Promise(resolve => {complete = resolve;});
    const preview = api.previewKnowledge();
    api.invalidateKnowledgePreview();
    complete({ok: true, json: async () => previewData()});
    await preview;
    assert.equal(document.getElementById('knowledge-import-btn').disabled, true);
    assert.equal(document.getElementById('knowledge-preview-panel').hidden, true);
});

test('manual import preserves multiline content as one document', async () => {
    const {api, context, document} = setup();
    document.getElementById('knowledge-docs').value = '# 制度\n版本：1.0\n## 审批\n正文';
    let payload;
    context.fetch = async (url, options) => {
        if (options) payload = JSON.parse(options.body);
        return {ok: true, json: async () => ({success: true, total_documents: 1})};
    };
    await api.addKnowledge();
    assert.deepEqual(payload.documents, ['# 制度\n版本：1.0\n## 审批\n正文']);
});

test('switching libraries clears chat, attachments and preview and scopes requests', async () => {
    const {context, document} = setup();
    await new Promise(resolve => setImmediate(resolve));
    const urls = [];
    context.fetch = async url => { urls.push(url); return {ok: true, json: async () => url.includes('stats') ? {total_documents: 0} : []}; };
    vm.runInContext('selectedFileUrl = "uploads/old.pdf"; knowledgePreview = {old:true}; lastAttempt = {old:true}', context);
    document.getElementById('knowledge-base').value = 'test';
    vm.runInContext('switchKnowledgeBase()', context);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(vm.runInContext('currentKnowledgeBase', context), 'test');
    assert.equal(vm.runInContext('selectedFileUrl', context), null);
    assert.equal(vm.runInContext('knowledgePreview', context), null);
    assert.equal(vm.runInContext('lastAttempt', context), null);
    assert(urls.includes('/api/conversations?knowledge_base=test'));
    assert(urls.includes('/api/knowledge/stats?knowledge_base=test'));
    vm.runInContext('knowledgeBusy = true', context);
    document.getElementById('knowledge-base').value = 'daily';
    vm.runInContext('switchKnowledgeBase()', context);
    assert.equal(document.getElementById('knowledge-base').value, 'test');
});

test('late history cannot populate a different library', async () => {
    const {context, document} = setup();
    await new Promise(resolve => setImmediate(resolve));
    let release;
    context.fetch = async url => {
        if (url.includes('/messages')) return new Promise(resolve => {release = resolve;});
        return {ok: true, json: async () => url.includes('stats') ? {} : []};
    };
    const pending = vm.runInContext('loadConversation("old")', context);
    document.getElementById('knowledge-base').value = 'test';
    vm.runInContext('switchKnowledgeBase()', context);
    release({ok: true, json: async () => [{role:'assistant', content:'旧库机密'}]});
    await pending;
    assert.equal(document.getElementById('chat').children.length, 0);
});

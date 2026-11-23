let currentKnowledgeBase = 'daily';
let viewRevision = 0;
function libraryURL(path, library = currentKnowledgeBase) {
    return library === 'daily' ? path : path + '?knowledge_base=' + library;
}
function switchKnowledgeBase() {
    const selector = document.getElementById('knowledge-base');
    if (isStreaming || isUploading || knowledgeBusy) {
        selector.value = currentKnowledgeBase;
        taskStatus('请等待当前任务完成后切换知识库。');
        return;
    }
    const next = selector.value;
    if (!['daily', 'test'].includes(next) || next === currentKnowledgeBase) return;
    currentKnowledgeBase = next;
    viewRevision++;
    closeSource();
    invalidateKnowledgePreview();
    for (const id of ['message', 'knowledge-docs', 'knowledge-query', 'knowledge-title', 'knowledge-version', 'knowledge-file', 'fileInput']) document.getElementById(id).value = '';
    for (const id of ['search-results', 'knowledge-status', 'context-warning']) document.getElementById(id).textContent = '';
    const banner = document.getElementById('library-banner');
    banner.textContent = next === 'test' ? '测试知识库：用于演示与验收，资料和会话与日常库分开。' : '日常知识库：仅检索本库资料。';
    banner.className = 'library-banner ' + next;
    newChat();
    loadKnowledgeStats();
}
let currentConversationId = null;
let isStreaming = false;
let isUploading = false;
let selectedFileUrl = null;
let activeController = null;
let lastAttempt = null;

function addMessage(text, role, meta = '') {
    const div = document.createElement('div');
    div.className = 'message ' + role;
    if (role === "assistant") renderCitations(div, text);
    else div.textContent = text;
    if (meta) {
        const detail = document.createElement('div');
        detail.className = 'meta';
        detail.textContent = meta;
        div.appendChild(detail);
    }
    const chat = document.getElementById('chat');
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
}

function updateMessage(element, text) {
    renderCitations(element, text);
    const chat = document.getElementById('chat');
    chat.scrollTop = chat.scrollHeight;
}

async function responseJSON(response) {
    const data = await response.json();
    if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入或服务状态');
    }
    return data;
}

async function loadConversations() {
    const revision = viewRevision;
    try {
        const conversations = await responseJSON(await fetch(libraryURL('/api/conversations')));
        if (revision !== viewRevision) return;
        const container = document.getElementById('conversations');
        container.replaceChildren();
        for (const c of conversations) {
            const item = document.createElement('button');
            item.className = 'conversation-item' + (c.id === currentConversationId ? ' active' : '');
            item.textContent = c.title || '未命名会话';
            item.onclick = () => loadConversation(c.id);
            container.appendChild(item);
        }
    } catch (err) {
        console.error('Failed to load conversations:', err);
    }
}

async function loadConversation(id) {
    if (isStreaming || isUploading) return;
    const revision = ++viewRevision;
    lastAttempt = null;
    document.getElementById("retry").hidden = true;
    currentConversationId = id;
    selectedFileUrl = null;
    document.getElementById('selected-file').textContent = '';
    document.getElementById('chat').replaceChildren();
    try {
        const messages = await responseJSON(await fetch(libraryURL('/api/conversations/' + encodeURIComponent(id) + '/messages')));
        if (revision !== viewRevision) return;
        messages.forEach(m => addMessage(m.content, m.role, m.attachment ? '附件：PDF' : ''));
        await loadConversations();
    } catch (err) {
        if (revision !== viewRevision) return;
        addMessage('加载失败：' + err.message, 'assistant');
    }
}

function newChat() {
    if (isStreaming || isUploading) return;
    viewRevision++;
    lastAttempt = null;
    taskStatus("就绪");
    document.getElementById("retry").hidden = true;
    currentConversationId = null;
    selectedFileUrl = null;
    document.getElementById('chat').replaceChildren();
    document.getElementById('selected-file').textContent = '';
    loadConversations();
}

async function handleFileSelect(event) {
    if (isStreaming || isUploading) return;
    const file = event.target.files[0];
    if (!file) return;
    selectedFileUrl = null;
    const status = document.getElementById('selected-file');
    if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 50 * 1024 * 1024) {
        status.textContent = '请选择不超过 50 MiB 的 PDF 文件。';
        event.target.value = '';
        return;
    }
    isUploading = true;
    document.getElementById('send').disabled = true;
    document.getElementById('uploadBtn').disabled = true;
    status.textContent = '正在上传…';
    const form = new FormData();
    form.append('file', file);
    try {
        const data = await responseJSON(await fetch('/api/chat/upload', {method: 'POST', body: form}));
        selectedFileUrl = data.file_url;
        status.textContent = '已选择：' + data.filename;
    } catch (err) {
        status.textContent = '上传失败：' + err.message;
    } finally {
        isUploading = false;
        document.getElementById('send').disabled = false;
        document.getElementById('uploadBtn').disabled = false;
        event.target.value = '';
    }
}

async function readEvents(body, onEvent) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
        while (true) {
            const {done, value} = await reader.read();
            buffer += decoder.decode(value, {stream: !done});
            let match;
            while ((match = /\r?\n\r?\n/.exec(buffer)) !== null) {
                const block = buffer.slice(0, match.index);
                buffer = buffer.slice(match.index + match[0].length);
                const data = block.split(/\r?\n/).filter(line => line.startsWith('data:'))
                    .map(line => line.slice(5).replace(/^ /, '')).join('\n');
                if (data && data !== '[DONE]') onEvent(JSON.parse(data));
            }
            if (done) break;
            if (buffer.length > 2 * 1024 * 1024) throw new Error('响应事件过大');
        }
        if (buffer.trim()) throw new Error('响应连接在事件结束前中断');
    } finally {
        await reader.cancel();
        reader.releaseLock();
    }
}

function taskStatus(text) {
    document.getElementById('task-status').textContent = text;
}

function showWarning(text) {
    const element = document.getElementById('context-warning');
    element.hidden = false;
    if (!element.textContent.includes(text)) element.textContent += (element.textContent ? '\n' : '') + text;
}

function cancelRequest() {
    if (!activeController) return;
    taskStatus('正在取消…');
    activeController.abort();
}

function retryLast() {
    if (!lastAttempt || !lastAttempt.canRetry) return;
    if (document.getElementById('message').value.trim() !== lastAttempt.text || selectedFileUrl !== lastAttempt.attachment) {
        taskStatus('内容已修改，请使用发送按钮提交新请求。');
        return;
    }
    return send(true);
}

async function send(retry = false) {
    if (isStreaming || isUploading) return;
    const input = document.getElementById('message');
    const text = input.value.trim() || (selectedFileUrl ? '请提取这份 PDF 的文字。' : '');
    if (!text) return;
    if (!retry) {
        addMessage(text, 'user');
        lastAttempt = {text, attachment: selectedFileUrl, accepted: false, canRetry: true,
            assistant: addMessage('正在处理…', 'assistant')};
    }
    const attempt = lastAttempt;
    isStreaming = true;
    activeController = new AbortController();
    document.getElementById('send').disabled = true;
    document.getElementById('uploadBtn').disabled = true;
    document.getElementById('cancel').hidden = false;
    document.getElementById('retry').hidden = true;
    const warning = document.getElementById('context-warning');
    warning.textContent = '';
    warning.hidden = true;
    taskStatus('正在准备问题和附件…');
    let completed = false;
    try {
        const response = await fetch('/api/chat/stream', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, signal: activeController.signal,
            body: JSON.stringify({knowledge_base: currentKnowledgeBase, message: text, conversation_id: currentConversationId,
                attachment: attempt.attachment, retry: retry && attempt.accepted})
        });
        if (!response.ok) {
            if (response.status === 409) attempt.canRetry = false;
            await responseJSON(response);
        }
        await readEvents(response.body, event => {
            if (event.knowledge_base && event.knowledge_base !== currentKnowledgeBase) throw new Error('响应知识库不匹配');
            if (event.conversation_id) {
                currentConversationId = event.conversation_id;
                attempt.accepted = true;
            }
            if (event.type === 'thinking') taskStatus('模型正在处理…');
            if (event.type === 'warning') showWarning(event.content);
            if (event.type === 'message') updateMessage(attempt.assistant, event.content);
            if (event.type === 'tool_call') {
                const labels = {knowledge_search: '检索知识库', read_file: '读取文件', sql_query: '查询数据库', pdf_extract: '提取 PDF', send_email: '发送邮件', write_file: '写入文件', http_request: '请求外部服务'};
                taskStatus('正在' + event.tools.map(t => labels[t.tool] || '使用工具').join('、') + '…');
                if (event.tools.some(t => ['send_email', 'write_file', 'http_request'].includes(t.tool))) {
                    attempt.canRetry = false;
                }
            }
            if (event.type === 'error') throw new Error(event.content);
            if (event.type === 'done') {
                completed = true;
                updateMessage(attempt.assistant, event.response);
                taskStatus('已完成');
            }
        });
        if (!completed) throw new Error('任务未完成，连接已中断');
        if (input.value.trim() === text) input.value = '';
        selectedFileUrl = null;
        document.getElementById('selected-file').textContent = '';
        lastAttempt = null;
    } catch (err) {
        const canceled = err.name === 'AbortError';
        if (!attempt.accepted && (canceled || err instanceof TypeError)) {
            attempt.canRetry = false;
            showWarning('任务接收状态尚未确认，请先查看历史记录，再决定是否重新提交。');
        }
        const message = canceled ? '已取消本次请求。' : '处理失败：' + err.message;
        updateMessage(attempt.assistant, message);
        taskStatus(message);
        if (!attempt.canRetry && attempt.accepted) showWarning('任务已尝试写入或发送操作，请先确认结果，再重新提交；此处不提供一键重试。');
        if (!input.value.trim()) input.value = text;
        document.getElementById('retry').hidden = !attempt.canRetry;
    } finally {
        isStreaming = false;
        activeController = null;
        document.getElementById('cancel').hidden = true;
        document.getElementById('send').disabled = false;
        document.getElementById('uploadBtn').disabled = false;
        await loadConversations();
    }
}

async function loadRuntime() {
    try {
        const data = await responseJSON(await fetch('/api/runtime'));
        document.getElementById('runtime-info').textContent = '本地模型：' + (data.model_label || data.model);
    } catch (err) {
        document.getElementById('runtime-info').textContent = '无法读取模型信息，请检查后端服务。';
    }
}

document.getElementById('message').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.isComposing) send();
});
loadConversations();
loadRuntime();

function switchTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelector('.tab[onclick="switchTab(\'' + tab + '\')"]').classList.add('active');
    document.getElementById(tab + '-tab').classList.add('active');
    if (tab === 'knowledge') loadKnowledgeStats();
}

async function loadKnowledgeStats() {
    const revision = viewRevision;
    const target = document.getElementById('knowledge-stats');
    try {
        const data = await responseJSON(await fetch(libraryURL('/api/knowledge/stats')));
        if (revision !== viewRevision) return;
        target.textContent = (currentKnowledgeBase === 'test' ? '测试' : '日常') + '知识库 · ' + (data.total_documents || 0) + ' 个资料片段';
    } catch (err) {
        if (revision !== viewRevision) return;
        target.textContent = '读取失败：' + err.message;
    }
}

async function addKnowledge() {
    if (knowledgeBusy) return;
    const textarea = document.getElementById('knowledge-docs');
    const status = document.getElementById('knowledge-status');
    const text = textarea.value.trim();
    if (!text) { status.textContent = '请先填写资料正文。'; return; }
    knowledgeBusy = true;
    status.textContent = '正在导入…';
    try {
        await responseJSON(await fetch(libraryURL('/api/knowledge/add'), {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({documents: [text]})
        }));
        status.textContent = '资料已导入，换行与段落已保留。';
        textarea.value = '';
        await loadKnowledgeStats();
    } catch (err) { status.textContent = '导入失败：' + err.message; }
    finally { knowledgeBusy = false; }
}

let knowledgePreview = null;
let knowledgeBusy = false;
let knowledgeRevision = 0;

function invalidateKnowledgePreview() {
    knowledgeRevision++;
    knowledgePreview = null;
    document.getElementById('knowledge-import-btn').disabled = true;
    document.getElementById('knowledge-preview-panel').hidden = true;
    document.getElementById('knowledge-file-status').textContent = '内容已变更，请生成预览。尚未写入知识库。';
}

async function previewKnowledge() {
    if (knowledgeBusy) return;
    const file = document.getElementById('knowledge-file').files[0];
    const status = document.getElementById('knowledge-file-status');
    invalidateKnowledgePreview();
    if (!file) { status.textContent = '请先选择资料文件。'; return; }
    if (!/\.(md|markdown|txt|pdf)$/i.test(file.name) || file.size > 50 * 1024 * 1024) {
        status.textContent = '请选择不超过 50 MiB 的 Markdown、TXT 或 PDF。'; return;
    }
    const revision = knowledgeRevision;
    const form = new FormData();
    form.append('file', file);
    form.append('title', document.getElementById('knowledge-title').value.trim());
    form.append('version', document.getElementById('knowledge-version').value.trim());
    knowledgeBusy = true;
    document.getElementById('knowledge-preview-btn').disabled = true;
    status.textContent = '正在提取文字，尚未写入知识库…';
    try {
        const data = await responseJSON(await fetch(libraryURL('/api/knowledge/preview'), {method: 'POST', body: form}));
        if (revision !== knowledgeRevision) return;
        knowledgePreview = data;
        const parts = data.payload.parts;
        const meta = parts[0].metadata;
        document.getElementById('knowledge-preview-meta').textContent = meta.filename + ' · 版本 ' + meta.version + ' · ' + parts.length + ' 个片段' + ((data.payload.warnings || []).length ? '。注意：' + data.payload.warnings.join('；') : '');
        document.getElementById('knowledge-preview-text').textContent = parts.map(p => p.content).join('\n\n────────\n\n');
        document.getElementById('preview-heading').textContent = '待导入内容';
        document.getElementById('knowledge-preview-panel').hidden = false;
        document.getElementById('knowledge-import-btn').disabled = false;
        status.textContent = '请核对下方全部内容，确认后再导入。预览有效期 30 分钟，服务重启后需重新预览。';
    } catch (err) {
        if (revision === knowledgeRevision) status.textContent = '预览失败：' + err.message;
    } finally {
        knowledgeBusy = false;
        document.getElementById('knowledge-preview-btn').disabled = false;
    }
}

async function importKnowledge() {
    if (knowledgeBusy || !knowledgePreview) return;
    const revision = knowledgeRevision;
    const preview = knowledgePreview;
    const status = document.getElementById('knowledge-file-status');
    knowledgeBusy = true;
    document.getElementById('knowledge-import-btn').disabled = true;
    document.getElementById('knowledge-preview-btn').disabled = true;
    status.textContent = '正在导入，请稍候…';
    for (const id of ['knowledge-file', 'knowledge-title', 'knowledge-version']) document.getElementById(id).disabled = true;
    try {
        const data = await responseJSON(await fetch(libraryURL('/api/knowledge/import'), {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(preview)
        }));
        if (revision === knowledgeRevision) {
            knowledgePreview = null;
            document.getElementById('preview-heading').textContent = data.added ? '已导入内容' : '已存在的资料';
            status.textContent = data.added ? '已导入 ' + data.added + ' 个片段，可以开始提问并点击引用核对原文。' : '相同内容已存在，未重复导入。';
        }
        await loadKnowledgeStats();
    } catch (err) {
        if (revision === knowledgeRevision) {
            status.textContent = '导入未确认：' + err.message + '。相同预览重复提交不会新增重复片段。';
            document.getElementById('knowledge-import-btn').disabled = false;
        }
    } finally {
        knowledgeBusy = false;
        for (const id of ['knowledge-file', 'knowledge-title', 'knowledge-version']) document.getElementById(id).disabled = false;
        document.getElementById('knowledge-preview-btn').disabled = false;
    }
}

function citationButton(id) {
    const library = currentKnowledgeBase;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'citation';
    button.textContent = id;
    button.setAttribute('aria-label', '查看引用原文 ' + id);
    button.onclick = () => openSource(id, library);
    return button;
}

function renderCitations(element, text) {
    const matches = [...text.matchAll(/\bKB-[a-f0-9]{16}\b/g)];
    if (!matches.length) { element.textContent = text; return; }
    element.replaceChildren();
    let cursor = 0;
    for (const match of matches) {
        const span = document.createElement('span');
        span.textContent = text.slice(cursor, match.index);
        element.appendChild(span);
        element.appendChild(citationButton(match[0]));
        cursor = match.index + match[0].length;
    }
    const tail = document.createElement('span');
    tail.textContent = text.slice(cursor);
    element.appendChild(tail);
}

let sourceRequest = 0;
function closeSource() { sourceRequest++; document.getElementById('source-dialog').close(); }

async function openSource(id, library = currentKnowledgeBase) {
    const request = ++sourceRequest;
    const dialog = document.getElementById('source-dialog');
    const title = document.getElementById('source-title');
    const meta = document.getElementById('source-meta');
    const text = document.getElementById('source-content');
    title.textContent = '正在读取原文';
    meta.textContent = id;
    text.textContent = '请稍候…';
    if (!dialog.open) dialog.showModal();
    try {
        const data = await responseJSON(await fetch(libraryURL('/api/knowledge/sources/' + encodeURIComponent(id), library)));
        if (request !== sourceRequest) return;
        const info = data.metadata || {};
        title.textContent = info.title || '知识库原文';
        meta.textContent = [id, info.filename || '旧资料未记录文件名', '版本 ' + (info.version || '未注明'),
            info.part ? '片段 ' + info.part + '/' + info.parts : '', info.page ? '第 ' + info.page + ' 页' : ''].filter(Boolean).join(' · ');
        text.textContent = data.original_content ?? data.content;
    } catch (err) {
        if (request !== sourceRequest) return;
        title.textContent = '暂时无法查看引用';
        text.textContent = err.message;
    }
}

document.getElementById('source-dialog').addEventListener('close', () => { sourceRequest++; });

async function searchKnowledge() {
    const revision = viewRevision;
    const query = document.getElementById('knowledge-query').value.trim();
    const target = document.getElementById('search-results');
    target.textContent = query ? '正在检索…' : '请先输入要查找的内容。';
    if (!query) return;
    try {
        const data = await responseJSON(await fetch(libraryURL('/api/knowledge/search'), {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query, top_k: 5})
        }));
        if (revision !== viewRevision) return;
        target.replaceChildren();
        for (const result of data.results || []) {
            const item = document.createElement('div');
            item.className = 'doc-item';
            item.appendChild(citationButton(result.source_id));
            const text = document.createElement('p');
            text.textContent = result.content.slice(0, 240) + (result.content.length > 240 ? '…' : '');
            item.appendChild(text);
            target.appendChild(item);
        }
        if (!data.results?.length) target.textContent = '没有检索到相关资料。';
    } catch (err) { if (revision !== viewRevision) return; target.textContent = '检索失败：' + err.message; }
}

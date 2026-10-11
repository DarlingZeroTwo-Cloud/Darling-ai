import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import 'bridge.dart';

/// 渲染消息种类
enum MsgKind { user, ai, tool, error, think }

/// 一条渲染消息
class ChatMsg {
  MsgKind kind;
  String text;
  String title;
  bool ok;
  ChatMsg(this.kind, this.text, {this.title = '', this.ok = true});
}

class AppState extends ChangeNotifier {
  final BridgeClient api;

  AppState(this.api);

  Map<String, dynamic> config = {};
  List<dynamic> providers = [];
  List<dynamic> models = [];
  Map<String, dynamic> settings = {};
  List<dynamic> conversations = [];
  String? activeConvId;
  String? currentModelId;

  List<ChatMsg> messages = [];
  bool busy = false;
  String thinkingText = '';
  bool askPending = false;
  String askCmd = '';
  int _seq = 0;

  ThemeMode get themeMode {
    final m = settings['theme_mode'];
    if (m == 'light') return ThemeMode.light;
    if (m == 'dark') return ThemeMode.dark;
    return ThemeMode.system;
  }

  /// UI 模式：compact=精简（当前样式），classic=经典（Zed 风格侧边栏 + 独立窗口）
  String get uiMode => (settings['ui_mode'] ?? 'compact').toString();

  Future<void> setUiMode(String mode) async {
    settings['ui_mode'] = mode;
    await saveSettings();
    notifyListeners();
  }

  Map<String, dynamic>? model(String? id) {
    if (id == null) return null;
    for (final m in models) {
      if (m['id'] == id) return m as Map<String, dynamic>;
    }
    return null;
  }

  Map<String, dynamic>? provider(String? id) {
    if (id == null) return null;
    for (final p in providers) {
      if (p['id'] == id) return p as Map<String, dynamic>;
    }
    return null;
  }

  Map<String, dynamic>? activeConversation() {
    for (final c in conversations) {
      if (c['id'] == activeConvId) return c as Map<String, dynamic>;
    }
    return null;
  }

  // ---------------- 初始化 ----------------
  Future<void> load() async {
    config = await api.getJson('/api/config');
    providers = (config['providers'] ?? []) as List;
    models = (config['models'] ?? []) as List;
    settings = ((config['settings'] ?? {}) as Map).cast<String, dynamic>();
    conversations = (config['conversations'] ?? []) as List;
    activeConvId = config['active_conv'] as String?;

    // 默认模型：活动会话记录的模型，否则第一个
    final conv = activeConversation();
    currentModelId = conv?['model_id'] as String?;
    if (currentModelId == null && models.isNotEmpty) {
      currentModelId = models.first['id'] as String;
    }
    _renderFromHist();
    notifyListeners();
  }

  Future<void> reload() async {
    config = await api.getJson('/api/config');
    providers = (config['providers'] ?? []) as List;
    models = (config['models'] ?? []) as List;
    settings = ((config['settings'] ?? {}) as Map).cast<String, dynamic>();
    conversations = (config['conversations'] ?? []) as List;
    activeConvId = config['active_conv'] as String?;
    notifyListeners();
  }

  // ---------------- 会话 ----------------
  Future<void> newConversation() async {
    if (busy) return;
    final r = await api.postJson('/api/convo/new', {'model_id': currentModelId});
    await reload();
    activeConvId = r['conv_id'] as String?;
    messages = [];
    notifyListeners();
  }

  Future<void> loadConversation(String convId) async {
    if (busy) return;
    final r = await api.postJson('/api/convo/load', {'conv_id': convId});
    await reload();
    activeConvId = convId;
    currentModelId = (r['model_id'] as String?) ?? currentModelId;
    if (currentModelId == null && models.isNotEmpty) {
      currentModelId = models.first['id'] as String;
    }
    _renderFromHist(r['hist'] as List?);
    notifyListeners();
  }

  Future<void> deleteConversation(String convId) async {
    await api.postJson('/api/convo/delete', {'conv_id': convId});
    if (activeConvId == convId) {
      activeConvId = null;
    }
    await reload();
    _renderFromHist();
    notifyListeners();
  }

  void setModel(String? modelId) {
    if (modelId == null) return;
    currentModelId = modelId;
    notifyListeners();
  }

  /// 删除一个模型（保存配置；若删的是当前模型则回退到第一个）
  Future<void> deleteModel(String modelId) async {
    models.removeWhere((m) => m['id'] == modelId);
    if (currentModelId == modelId) {
      currentModelId = models.isNotEmpty ? models.first['id'] as String? : null;
    }
    await saveProvidersModels();
    notifyListeners();
  }

  // ---------------- 渲染 ----------------
  void _renderFromHist([List? hist]) {
    messages = [];
    final h = hist ?? (activeConversation()?['hist'] as List? ?? []);
    if (h.isEmpty) return; // 空历史就保持空，走空状态 logo
    for (final raw in h) {
      final msg = raw as Map<String, dynamic>;
      final role = msg['role'];
      final content = (msg['content'] ?? '').toString();
      if (role == 'system') continue;
      if (content.startsWith('[命令执行结果]')) {
        final p = _parseNote(content);
        messages.add(ChatMsg(MsgKind.tool, p['out'] ?? '',
            title: '执行命令 · ${p['cmd']}', ok: !content.contains('失败')));
        continue;
      }
      if (content.startsWith('[插件执行结果]')) {
        final p = _parseNote(content);
        messages.add(ChatMsg(MsgKind.tool, p['out'] ?? '',
            title: '插件 · ${p['plugin']}/${p['tool']}', ok: !content.contains('失败')));
        continue;
      }
      if (content.startsWith('[插件调用失败]') ||
          content.startsWith('用户取消了') ||
          content.startsWith('用户没有回应')) {
        messages.add(ChatMsg(MsgKind.tool, content, ok: false));
        continue;
      }
      messages.add(ChatMsg(role == 'user' ? MsgKind.user : MsgKind.ai, content));
    }
  }

  Map<String, String> _parseNote(String content) {
    final out = <String, String>{};
    final lines = content.split('\n');
    for (var i = 0; i < lines.length; i++) {
      final l = lines[i];
      if (l.startsWith('命令: ')) out['cmd'] = l.substring(4);
      if (l.startsWith('插件: ')) out['plugin'] = l.substring(4);
      if (l.startsWith('工具: ')) out['tool'] = l.substring(4);
      if (l.startsWith('输出: ')) out['out'] = lines.sublist(i + 1).join('\n');
      if (l.startsWith('结果: ')) out['out'] = lines.sublist(i + 1).join('\n');
    }
    return out;
  }

  // ---------------- 对话 ----------------
  /// 发送消息，可带多模态图片（data_url 列表）
  Future<void> send(String text, {List<Map<String, dynamic>> images = const []}) async {
    final m = model(currentModelId);
    if (m == null || busy || text.trim().isEmpty) return;
    busy = true;
    askPending = false;
    thinkingText = '正在连接服务器…';
    _seq = 0;
    String? jobId;
    messages.add(ChatMsg(MsgKind.user, text));
    notifyListeners();

    try {
      await api.chat(
        m['id'] as String,
        text,
        images: images,
        onEvent: (ev) {
          switch (ev['type']) {
            case 'state':
              thinkingText = (ev['text'] ?? '').toString();
              break;
            case 'step':
              thinkingText = (ev['text'] ?? '任务执行中…').toString();
              break;
            case 'token':
              if (messages.isEmpty || messages.last.kind != MsgKind.ai) {
                messages.add(ChatMsg(MsgKind.ai, ''));
              }
              messages.last.text += (ev['text'] ?? '').toString();
              break;
            case 'tool':
              final t = (ev['title'] ?? '').toString();
              final c = (ev['content'] ?? '').toString();
              final ok = ev['ok'] == true;
              if (c.isNotEmpty) {
                messages.add(ChatMsg(MsgKind.tool, c, title: t, ok: ok));
              }
              break;
            case 'ask':
              askPending = true;
              askCmd = (ev['cmd'] ?? '').toString();
              break;
            case 'error':
              final e = (ev['text'] ?? '').toString();
              messages.add(ChatMsg(MsgKind.error, e));
              break;
          }
          notifyListeners();
        },
        onJob: (id) {
          jobId = id;
          _jobId = id;
        },
      );
      // 完成：恢复默认 thinking（无流时占位）
      if (messages.isEmpty || messages.last.kind != MsgKind.ai) {
        messages.add(ChatMsg(MsgKind.think, ''));
      }
      thinkingText = '';
      await reload();
      // 同步活动会话标题到列表
      notifyListeners();
    } catch (e) {
      messages.add(ChatMsg(MsgKind.error, '对话中断：$e'));
    } finally {
      busy = false;
      askPending = false;
      thinkingText = '';
      notifyListeners();
    }
  }

  /// 导出当前会话（Markdown）
  Future<String?> exportConversation() async {
    final convId = activeConvId;
    if (convId == null) return null;
    try {
      final r = await api.convoExport(convId);
      if (r['ok'] == true) return r['markdown'] as String?;
    } catch (_) {}
    return null;
  }

  Future<void> answerAsk(bool allow) async {
    final jobId = _jobId;
    askPending = false;
    notifyListeners();
    if (jobId != null) {
      try {
        await api.postJson('/api/decision', {'job': jobId, 'allow': allow});
      } catch (_) {}
    }
  }

  String? _jobId;

  // ---------------- 设置 ----------------
  Future<void> saveSettings() async {
    await api.postJson('/api/config', {'settings': settings});
    await reload();
    notifyListeners();
  }

  Future<void> setThemeMode(String mode) async {
    settings['theme_mode'] = mode;
    await saveSettings();
    notifyListeners();
  }

  Future<void> saveProvidersModels() async {
    await api.postJson('/api/config', {
      'providers': providers,
      'models': models,
    });
    await reload();
    notifyListeners();
  }
}

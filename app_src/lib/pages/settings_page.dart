import 'dart:io';
import 'package:flutter/material.dart';

import '../main.dart';
import '../state.dart';
import '../dialogs/prompt_dialog.dart';
import '../widgets/spin_field.dart';

class SettingsPage extends StatelessWidget {
  final AppState state;
  const SettingsPage({super.key, required this.state});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: state,
      builder: (context, _) {
        final s = state.settings;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 20, 24, 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('设置',
                      style: TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w700,
                          color: Theme.of(context).colorScheme.onSurface)),
                  const SizedBox(height: 2),
                  Text('调整 AI 行为与网络参数',
                      style: TextStyle(
                          fontSize: 13,
                          color: Theme.of(context).colorScheme.onSurfaceVariant)),
                ],
              ),
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
                children: [
                  _Card(
                    title: '对话',
                    children: [
                      _Row(
                        label: '温度 temperature',
                        desc: '控制回复随机性（0 ~ 2），默认 0.7。',
                        trailing: _TempSlider(state: state),
                      ),
                      _Row(
                        label: '最大回复 tokens',
                        desc: '单次回复长度上限。',
                        trailing: SpinField(
                          min: 256,
                          max: 131072,
                          value: (s['max_tokens'] as num? ?? 4096).toDouble(),
                          step: 512,
                          onChanged: (v) {
                            state.settings['max_tokens'] = v.toInt();
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '思考强度',
                        desc: 'o 系列 / DeepSeek R1 等支持该参数的模型生效；不支持时请选“不发送”。',
                        trailing: _EffortMenu(state: state),
                      ),
                      _Row(
                        label: 'Top-P 核采样',
                        desc: '默认 1.0（不发送该参数）。',
                        trailing: SpinField(
                          min: 0.01,
                          max: 1.0,
                          value: (s['top_p'] as num? ?? 1.0).toDouble(),
                          step: 0.05,
                          decimals: 2,
                          onChanged: (v) {
                            state.settings['top_p'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '系统提示词',
                        desc: '定义 AI 的角色与能力边界。',
                        trailing: OutlinedButton(
                          onPressed: () => showDialog(
                            context: context,
                            builder: (_) => PromptDialog(state: state),
                          ),
                          child: const Text('查看 / 编辑'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '网络',
                    children: [
                      _Row(
                        label: '连接超时（秒）',
                        desc: '连不上时快速失败并重试，避免一直等待。',
                        trailing: SpinField(
                          min: 3,
                          max: 120,
                          value: (s['connect_timeout'] as num? ?? 15).toDouble(),
                          step: 1,
                          onChanged: (v) {
                            state.settings['connect_timeout'] = v.toInt();
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: 'API 失败重试次数',
                        desc: '流式请求失败后的额外重试次数（0 ~ 5）。',
                        trailing: SpinField(
                          min: 0,
                          max: 5,
                          value: (s['api_retries'] as num? ?? 1).toDouble(),
                          step: 1,
                          onChanged: (v) {
                            state.settings['api_retries'] = v.toInt();
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '使用系统代理',
                        desc: '默认直连，避免残留代理导致连不上服务器。需要走代理时再开启。',
                        trailing: Switch(
                          value: s['use_proxy'] == true,
                          onChanged: (v) {
                            state.settings['use_proxy'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '访问权限',
                    children: [
                      _Row(
                        label: 'AI 访问模式',
                        desc: '完全访问：命令自动执行；按需访问：每次执行前询问。',
                        trailing: SegmentedButton<String>(
                          segments: const [
                            ButtonSegment(value: 'full', label: Text('完全访问')),
                            ButtonSegment(value: 'on_demand', label: Text('按需访问')),
                          ],
                          selected: {s['access_mode'] ?? 'on_demand'},
                          onSelectionChanged: (sel) {
                            state.settings['access_mode'] = sel.first;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '默认工作区',
                        desc: 'AI 执行命令、读写文件的目录。',
                        trailing: OutlinedButton(
                          onPressed: () => _pickWorkspace(context),
                          child: const Text('选择目录…'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '对话稳定性',
                    children: [
                      _Row(
                        label: '上下文自动压缩',
                        desc: '历史过长时自动截断并保留要点，防止对话中断 / 超长报错。',
                        trailing: Switch(
                          value: s['context_auto_compress'] != false,
                          onChanged: (v) {
                            state.settings['context_auto_compress'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '压缩阈值 tokens',
                        desc: '超过该长度时触发压缩（保留首条系统指令 + 尾部最近内容）。',
                        trailing: SpinField(
                          min: 2048,
                          max: 262144,
                          value:
                              (s['context_max_tokens'] as num? ?? 24000).toDouble(),
                          step: 1024,
                          onChanged: (v) {
                            state.settings['context_max_tokens'] = v.toInt();
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '流式自动重连',
                        desc: '网络波动导致流中断时自动重连，减少“对话中断”。',
                        trailing: Switch(
                          value: s['auto_reconnect'] != false,
                          onChanged: (v) {
                            state.settings['auto_reconnect'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '读取超时（秒）',
                        desc: '单次流式读取的最大等待时间，防止长时间无响应。',
                        trailing: SpinField(
                          min: 10,
                          max: 600,
                          value: (s['read_timeout'] as num? ?? 120).toDouble(),
                          step: 10,
                          onChanged: (v) {
                            state.settings['read_timeout'] = v.toInt();
                            state.saveSettings();
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '智能与记忆',
                    children: [
                      _Row(
                        label: '跨会话长期记忆',
                        desc: '对话结束后自动沉淀要点，下次对话自动带入，让 AI“记得你”。',
                        trailing: Switch(
                          value: s['memory_enabled'] == true,
                          onChanged: (v) {
                            state.settings['memory_enabled'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '任务模式',
                        desc: '开启后 AI 会规划多步任务并逐步执行（配合文件 / git / 容器工具）。',
                        trailing: Switch(
                          value: s['task_mode'] == true,
                          onChanged: (v) {
                            state.settings['task_mode'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '长期记忆库',
                        desc: '查看 / 清空已沉淀的跨会话记忆。',
                        trailing: OutlinedButton(
                          onPressed: () async {
                            try {
                              final r = await state.api.memoryList();
                              final items = (r['items'] ?? []) as List;
                              await showDialog(
                                context: context,
                                builder: (ctx) => AlertDialog(
                                  title: const Text('长期记忆'),
                                  content: SizedBox(
                                    width: 420,
                                    child: items.isEmpty
                                        ? const Text('暂无记忆')
                                        : Column(
                                            mainAxisSize: MainAxisSize.min,
                                            crossAxisAlignment:
                                                CrossAxisAlignment.start,
                                            children: [
                                              for (final it in items
                                                      .take(20)
                                                      .toList())
                                                Padding(
                                                  padding: const EdgeInsets
                                                      .symmetric(vertical: 4),
                                                  child: Text(
                                                    '• ${it['tag']}：${it['summary']}',
                                                    style: const TextStyle(
                                                        fontSize: 13),
                                                  ),
                                                ),
                                            ],
                                          ),
                                  ),
                                  actions: [
                                    TextButton(
                                        onPressed: () =>
                                            Navigator.pop(ctx),
                                        child: const Text('关闭')),
                                    TextButton(
                                      onPressed: () async {
                                        await state.api.memoryClear();
                                        Navigator.pop(ctx);
                                      },
                                      child: const Text('清空',
                                          style: TextStyle(
                                              color: Colors.redAccent)),
                                    ),
                                  ],
                                ),
                              );
                            } catch (_) {}
                          },
                          child: const Text('查看 / 清空'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '本地推理',
                    children: [
                      _Row(
                        label: 'GPU 加速',
                        desc: '自动检测 / 纯 CPU / Vulkan（兼容 N/A/I 卡）。需要重启后端生效。',
                        trailing: SegmentedButton<String>(
                          segments: const [
                            ButtonSegment(value: 'cpu', label: Text('CPU')),
                            ButtonSegment(value: 'auto', label: Text('自动')),
                            ButtonSegment(value: 'vulkan', label: Text('Vulkan')),
                          ],
                          selected: {s['local_gpu_mode'] ?? 'cpu'},
                          onSelectionChanged: (sel) {
                            state.settings['local_gpu_mode'] = sel.first;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: 'GPU offload 层数',
                        desc: '0 = 纯 CPU；35 = 中型模型推荐；999 = 全部层卸载。',
                        trailing: SizedBox(
                          width: 200,
                          child: Row(mainAxisSize: MainAxisSize.min, children: [
                            Expanded(
                              child: Slider(
                                value: (s['local_gpu_layers'] as num? ?? 0)
                                    .toDouble()
                                    .clamp(0, 999),
                                min: 0,
                                max: 999,
                                divisions: 999,
                                label: '${s['local_gpu_layers'] ?? 0}',
                                onChanged: (v) {
                                  state.settings['local_gpu_layers'] = v.toInt();
                                  state.saveSettings();
                                },
                              ),
                            ),
                            SizedBox(
                              width: 36,
                              child: Text(
                                  '${s['local_gpu_layers'] ?? 0}',
                                  style: const TextStyle(fontSize: 13)),
                            ),
                          ]),
                        ),
                      ),
                      _Row(
                        label: '上下文长度',
                        desc: '本地模型单次可处理的 token 数，越大越占内存。',
                        trailing: SpinField(
                          min: 512,
                          max: 131072,
                          value: (s['local_n_ctx'] as num? ?? 4096).toDouble(),
                          step: 512,
                          onChanged: (v) {
                            state.settings['local_n_ctx'] = v.toInt();
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '使用 HF 国内镜像',
                        desc: '模型市场下载走 hf-mirror.com（国内推荐开启）。',
                        trailing: Switch(
                          value: s['hf_mirror'] != false,
                          onChanged: (v) {
                            state.settings['hf_mirror'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '服务监听',
                    children: [
                      _Row(
                        label: '监听地址',
                        desc: '127.0.0.1 = 仅本机；0.0.0.0 = 局域网其他设备可连；或填指定 IP。重启后端生效。',
                        trailing: SizedBox(
                          width: 200,
                          child: TextField(
                            controller: TextEditingController(
                                text: (s['bind_host'] ?? '127.0.0.1').toString()),
                            decoration: const InputDecoration(
                                isDense: true, hintText: '127.0.0.1'),
                            onSubmitted: (v) {
                              state.settings['bind_host'] = v.trim();
                              state.saveSettings();
                            },
                          ),
                        ),
                      ),
                      _Row(
                        label: 'API Key',
                        desc: '开启后，非本机访问必须带 X-API-Key 头。留空则不校验。',
                        trailing: SizedBox(
                          width: 220,
                          child: TextField(
                            controller: TextEditingController(
                                text: (s['api_key'] ?? '').toString()),
                            decoration: const InputDecoration(
                                isDense: true, hintText: '留空=不校验'),
                            onSubmitted: (v) {
                              state.settings['api_key'] = v.trim();
                              state.saveSettings();
                            },
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '外观',
                    children: [
                      _Row(
                        label: 'UI 模式',
                        desc: '精简：顶部菜单切换页面；经典：左侧历史对话栏，设置/模型/插件/本地独立窗口打开。',
                        trailing: SegmentedButton<String>(
                          segments: const [
                            ButtonSegment(value: 'compact', label: Text('精简')),
                            ButtonSegment(value: 'classic', label: Text('经典')),
                          ],
                          selected: {state.uiMode},
                          onSelectionChanged: (sel) {
                            state.setUiMode(sel.first);
                          },
                        ),
                      ),
                      const SizedBox(height: 14),
                      _Row(
                        label: '界面主题',
                        desc: '跟随系统，或手动固定深浅色。',
                        trailing: SegmentedButton<String>(
                          segments: const [
                            ButtonSegment(value: 'system', label: Text('跟随系统')),
                            ButtonSegment(value: 'light', label: Text('浅色')),
                            ButtonSegment(value: 'dark', label: Text('深色')),
                          ],
                          selected: {s['theme_mode'] ?? 'system'},
                          onSelectionChanged: (sel) {
                            state.setThemeMode(sel.first);
                          },
                        ),
                      ),
                      const SizedBox(height: 14),
                      // 个性化背景
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 6),
                        child: Row(
                          children: [
                            const SizedBox(
                              width: 120,
                              child: Text('背景图片', style: TextStyle(fontSize: 13)),
                            ),
                            Expanded(
                              child: TextFormField(
                                initialValue: s['bg_image'] ?? '',
                                decoration: const InputDecoration(
                                  hintText: '留空 = 纯色背景',
                                  isDense: true,
                                ),
                                onChanged: (v) {
                                  state.settings['bg_image'] = v.trim();
                                  state.saveSettings();
                                },
                              ),
                            ),
                            const SizedBox(width: 8),
                            OutlinedButton(
                              onPressed: () async {
                                // 用 PowerShell 弹文件选择框
                                final result = await Process.run(
                                  'powershell',
                                  [
                                    '-NoProfile',
                                    '-Command',
                                    "Add-Type -AssemblyName System.Windows.Forms; "
                                        "\$d = New-Object System.Windows.Forms.OpenFileDialog; "
                                        "\$d.Filter = '图片|*.jpg;*.jpeg;*.png;*.bmp'; "
                                        "if (\$d.ShowDialog() -eq 'OK') { \$d.FileName }"
                                  ],
                                );
                                final path = (result.stdout as String).trim();
                                if (path.isNotEmpty &&
                                    (path.toLowerCase().endsWith('.jpg') ||
                                     path.toLowerCase().endsWith('.jpeg') ||
                                     path.toLowerCase().endsWith('.png') ||
                                     path.toLowerCase().endsWith('.bmp'))) {
                                  state.settings['bg_image'] = path;
                                  state.saveSettings();
                                }
                              },
                              child: const Text('浏览'),
                            ),
                          ],
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 6),
                        child: Row(
                          children: [
                            const SizedBox(
                              width: 120,
                              child: Text('背景不透明度', style: TextStyle(fontSize: 13)),
                            ),
                            Expanded(
                              child: Slider(
                                value: (s['bg_opacity'] ?? 0.85).toDouble(),
                                min: 0.3,
                                max: 1.0,
                                divisions: 7,
                                label: ((s['bg_opacity'] ?? 0.85)).toStringAsFixed(2),
                                onChanged: (v) {
                                  state.settings['bg_opacity'] = v;
                                  state.saveSettings();
                                },
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: 'MCP 服务',
                    children: [
                      _Row(
                        label: 'MCP 工具调用',
                        desc: '连接外部 MCP 服务器（如文件系统、数据库、浏览器工具），AI 可直接调用。',
                        trailing: Switch(
                          value: s['mcp_enabled'] != false,
                          onChanged: (v) {
                            state.settings['mcp_enabled'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: 'MCP 服务器',
                        desc: '管理已配置的 MCP server（stdio 方式）。',
                        trailing: OutlinedButton(
                          onPressed: () async {
                            try {
                              final r = await state.api.mcpList();
                              final servers = (r['servers'] ?? []) as List;
                              await showDialog(
                                context: context,
                                builder: (ctx) => AlertDialog(
                                  title: const Text('MCP 服务器'),
                                  content: SizedBox(
                                    width: 460,
                                    child: Column(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        if (servers.isEmpty)
                                          const Padding(
                                            padding: EdgeInsets.all(16),
                                            child: Text('暂无 MCP 服务器'),
                                          )
                                        else
                                          for (final sv in servers)
                                            ListTile(
                                              dense: true,
                                              title: Text(
                                                  '${sv['name']}',
                                                  style: const TextStyle(
                                                      fontSize: 13)),
                                              subtitle: Text(
                                                  '命令: ${sv['command']}',
                                                  style: const TextStyle(
                                                      fontSize: 12)),
                                              trailing: IconButton(
                                                icon: const Icon(
                                                    Icons.delete_outline,
                                                    size: 18),
                                                onPressed: () async {
                                                  await state.api
                                                      .mcpDelete(
                                                          sv['name']);
                                                  if (ctx.mounted) {
                                                    Navigator.pop(ctx);
                                                  }
                                                },
                                              ),
                                            ),
                                        const SizedBox(height: 8),
                                        const Divider(height: 1),
                                        const SizedBox(height: 8),
                                        Row(
                                          children: [
                                            Expanded(
                                              child: TextButton(
                                                onPressed: () async {
                                                  final ctl =
                                                      TextEditingController();
                                                  final cmdCtl =
                                                      TextEditingController();
                                                  final res =
                                                      await showDialog<String?>(
                                                    context: ctx,
                                                    builder: (dctx) =>
                                                        AlertDialog(
                                                      title: const Text(
                                                          '添加 MCP 服务器'),
                                                      content: Column(
                                                        mainAxisSize:
                                                            MainAxisSize.min,
                                                        children: [
                                                          TextField(
                                                            controller: ctl,
                                                            decoration:
                                                                const InputDecoration(
                                                              labelText:
                                                                  '名称',
                                                              hintText:
                                                                  '例如 filesystem',
                                                            ),
                                                          ),
                                                          const SizedBox(
                                                              height: 10),
                                                          TextField(
                                                            controller:
                                                                cmdCtl,
                                                            decoration:
                                                                const InputDecoration(
                                                              labelText:
                                                                  '启动命令',
                                                              hintText:
                                                                  '例如 npx -y @modelcontextprotocol/server-filesystem D:/workspace',
                                                            ),
                                                          ),
                                                        ],
                                                      ),
                                                      actions: [
                                                        TextButton(
                                                            onPressed: () =>
                                                                Navigator.pop(
                                                                    dctx),
                                                            child: const Text(
                                                                '取消')),
                                                        FilledButton(
                                                          onPressed: () =>
                                                              Navigator.pop(
                                                                  dctx,
                                                                  '${ctl.text.trim()}\u0001${cmdCtl.text.trim()}'),
                                                          child:
                                                              const Text('添加'),
                                                        ),
                                                      ],
                                                    ),
                                                  );
                                                  if (res != null &&
                                                      res
                                                          .split('\u0001')
                                                          .first
                                                          .isNotEmpty &&
                                                      res
                                                          .split('\u0001')
                                                          .last
                                                          .isNotEmpty) {
                                                    await state.api.mcpSave({
                                                      'name': res
                                                          .split('\u0001')
                                                          .first,
                                                      'command': res
                                                          .split('\u0001')
                                                          .last,
                                                    });
                                                  }
                                                  if (ctx.mounted) {
                                                    Navigator.pop(ctx);
                                                  }
                                                },
                                                child:
                                                    const Text('添加服务器'),
                                              ),
                                            ),
                                          ],
                                        ),
                                      ],
                                    ),
                                  ),
                                  actions: [
                                    TextButton(
                                        onPressed: () =>
                                            Navigator.pop(ctx),
                                        child: const Text('关闭')),
                                  ],
                                ),
                              );
                            } catch (_) {}
                          },
                          child: const Text('管理…'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '容器工作区',
                    children: [
                      _Row(
                        label: '运行方式',
                        desc: '本机：AI 直接在电脑上执行；容器：在 WSL / Docker 沙箱中执行。',
                        trailing: SegmentedButton<String>(
                          segments: const [
                            ButtonSegment(value: 'host', label: Text('本机')),
                            ButtonSegment(
                                value: 'container', label: Text('容器')),
                          ],
                          selected: {s['container_mode'] ?? 'host'},
                          onSelectionChanged: (sel) {
                            state.settings['container_mode'] = sel.first;
                            state.saveSettings();
                          },
                        ),
                      ),
                      _Row(
                        label: '容器环境',
                        desc: 'Windows 使用 WSL 发行版；Linux 使用 Docker 镜像。',
                        trailing: OutlinedButton(
                          onPressed: () => _manageContainer(context, state),
                          child: const Text('检测 / 配置…'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '内置浏览器',
                    children: [
                      _Row(
                        label: '启用联网浏览',
                        desc: 'AI 可打开网页并抓取正文；也支持从侧边栏打开浏览器面板。',
                        trailing: Switch(
                          value: s['browser_enabled'] != false,
                          onChanged: (v) {
                            state.settings['browser_enabled'] = v;
                            state.saveSettings();
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _Card(
                    title: '数据',
                    children: [
                      _Row(
                        label: '清除对话历史',
                        desc: '清空所有对话与保存的历史。',
                        trailing: OutlinedButton(
                          onPressed: () => _clearHistory(context),
                          style: OutlinedButton.styleFrom(
                            foregroundColor:
                                Theme.of(context).colorScheme.error,
                            side: BorderSide(
                                color: Theme.of(context)
                                    .colorScheme
                                    .error
                                    .withValues(alpha: 0.4)),
                          ),
                          child: const Text('清除历史'),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }

  Future<void> _pickWorkspace(BuildContext context) async {
    final ctl = TextEditingController(
        text: (state.settings['workspace'] ?? '').toString());
    final val = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('设置工作区'),
        content: TextField(
          controller: ctl,
          autofocus: true,
          decoration: const InputDecoration(
            labelText: '工作区目录路径',
            hintText: r'例如 D:\workspace 或 /home/user/workspace',
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, ctl.text),
              child: const Text('确定')),
        ],
      ),
    );
    if (val != null && val.trim().isNotEmpty) {
      state.settings['workspace'] = val.trim();
      await state.saveSettings();
    }
  }

  Future<void> _manageContainer(BuildContext context, AppState state) async {
    String status = '正在检测…';
    List<Map<String, dynamic>> distros = [];
    bool isWindows = false;
    bool dockerOk = false;
    try {
      final d = await state.api.containerDetect();
      isWindows = d['platform'] == 'Windows';
      distros = ((d['wsl_distros'] ?? []) as List)
          .map((e) => (e as Map).cast<String, dynamic>())
          .toList();
      dockerOk = d['docker'] == true;
      if (isWindows) {
        status = distros.isEmpty
            ? '未检测到 WSL 发行版'
            : '检测到 ${distros.length} 个 WSL 发行版';
      } else {
        status = dockerOk ? 'Docker 已安装' : '未检测到 Docker';
      }
    } catch (e) {
      status = '检测失败: $e';
    }

    final isWin = isWindows;
    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSt) => AlertDialog(
          title: const Text('容器环境配置'),
          content: SizedBox(
            width: 440,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(status, style: const TextStyle(fontSize: 13)),
                const SizedBox(height: 12),
                if (isWin && distros.isNotEmpty) ...[
                  const Text('选择 WSL 发行版：',
                      style: TextStyle(fontSize: 13)),
                  const SizedBox(height: 6),
                  DropdownMenu<String>(
                    initialSelection:
                        (state.settings['container_target'] ?? '').toString(),
                    dropdownMenuEntries: [
                      for (final d in distros)
                        DropdownMenuEntry(
                            value: d['name'].toString(),
                            label:
                                '${d['name']} (${d['state']})'),
                    ],
                    onSelected: (v) {
                      if (v != null) {
                        state.settings['container_target'] = v;
                        state.saveSettings();
                      }
                    },
                  ),
                ],
                if (isWin && distros.isEmpty) ...[
                  OutlinedButton(
                    onPressed: () {
                      Process.start(
                          'cmd', ['/c', 'start', 'ms-windows-store://pdp/?ProductId=9N9P8S1DZ5G1']);
                    },
                    child: const Text('去微软商店安装 WSL'),
                  ),
                ],
                if (!isWin) ...[
                  const Text('Docker 镜像：',
                      style: TextStyle(fontSize: 13)),
                  const SizedBox(height: 6),
                  TextFormField(
                    initialValue: (state.settings['container_image']
                            ??
                        'ubuntu:latest')
                        .toString(),
                    decoration: const InputDecoration(
                        isDense: true, hintText: 'ubuntu:latest'),
                    onFieldSubmitted: (v) {
                      state.settings['container_image'] = v.trim();
                      state.saveSettings();
                    },
                  ),
                  const SizedBox(height: 10),
                  if (!dockerOk)
                    OutlinedButton(
                      onPressed: () async {
                        setSt(() => status = '正在自动安装 Docker 并配置国内镜像加速…');
                        try {
                          final r = await state.api.containerInstallDocker();
                          setSt(() => status =
                              r['ok'] == true
                                  ? '安装完成：${r['output']}'
                                  : '安装失败：${r['output']}');
                        } catch (e) {
                          setSt(() => status = '安装异常: $e');
                        }
                      },
                      child: const Text('自动安装 Docker + 镜像加速'),
                    ),
                ],
                const SizedBox(height: 8),
                Text(
                  'AI 在容器模式下执行的所有命令都运行在沙箱内，无法直接修改你的系统文件。',
                  style: TextStyle(
                      fontSize: 12,
                      color: Theme.of(ctx).colorScheme.onSurfaceVariant),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('关闭')),
          ],
        ),
      ),
    );
  }

  Future<void> _clearHistory(BuildContext context) async {    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清除对话历史'),
        content: const Text('确定清空所有对话与保存的历史？此操作不可恢复。'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('取消')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              style: FilledButton.styleFrom(
                  backgroundColor: Theme.of(ctx).colorScheme.error),
              child: const Text('清除'),
          ),
        ],
      ),
    );
    if (ok == true) {
      await state.api.postJson('/api/config', {
        'histories': <String, dynamic>{},
        'conversations': <dynamic>[],
        'active_conv': null,
      });
      await state.reload();
    }
  }
}

class _Card extends StatelessWidget {
  final String title;
  final List<Widget> children;
  const _Card({required this.title, required this.children});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title,
                style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: cs.onSurface)),
            const SizedBox(height: 6),
            ...children,
          ],
        ),
      ),
    );
  }
}

class _Row extends StatelessWidget {
  final String label;
  final String desc;
  final Widget trailing;
  const _Row({required this.label, required this.desc, required this.trailing});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label,
                    style: TextStyle(
                        fontSize: 13.5,
                        fontWeight: FontWeight.w600,
                        color: cs.onSurface)),
                const SizedBox(height: 2),
                Text(desc,
                    style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              ],
            ),
          ),
          const SizedBox(width: 16),
          trailing,
        ],
      ),
    );
  }
}

class _TempSlider extends StatelessWidget {
  final AppState state;
  const _TempSlider({required this.state});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final v = (state.settings['temperature'] as num? ?? 0.7).toDouble();
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          width: 170,
          child: Slider(
            value: v.clamp(0, 2),
            min: 0,
            max: 2,
            divisions: 20,
            onChanged: (nv) {
              state.settings['temperature'] = (nv * 10).round() / 10.0;
              state.saveSettings();
            },
          ),
        ),
        SizedBox(
          width: 34,
          child: Text(v.toStringAsFixed(1),
              textAlign: TextAlign.center,
              style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: cs.onSurface)),
        ),
      ],
    );
  }
}

class _EffortMenu extends StatelessWidget {
  final AppState state;
  const _EffortMenu({required this.state});

  @override
  Widget build(BuildContext context) {
    final eff = (state.settings['reasoning_effort'] ?? 'off').toString();
    const options = [
      ('off', '不发送'),
      ('low', '低 low'),
      ('medium', '中 medium'),
      ('high', '高 high'),
    ];
    return DropdownMenu<String>(
      initialSelection: eff,
      width: 140,
      label: const Text('思考强度'),
      dropdownMenuEntries: [
        for (final (k, v) in options) DropdownMenuEntry(value: k, label: v),
      ],
      onSelected: (v) {
        if (v != null) {
          state.settings['reasoning_effort'] = v;
          state.saveSettings();
        }
      },
    );
  }
}

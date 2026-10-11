import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../main.dart';
import '../state.dart';
import '../widgets/message_bubble.dart';
import '../widgets/thinking_row.dart';
import '../widgets/tool_card.dart';
import '../dialogs/model_picker.dart';

class ChatPage extends StatelessWidget {
  final AppState state;
  const ChatPage({super.key, required this.state});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: state,
      builder: (context, _) {
        final empty = state.messages.isEmpty && !state.busy;
        return Column(
          children: [
            Expanded(
              child: empty ? _EmptyState(state: state) : _MessageList(state: state),
            ),
            if (!empty) _Composer(state: state),
          ],
        );
      },
    );
  }
}

// ==================== 空状态（opencode 中央 logo + 居中输入框） ====================
class _EmptyState extends StatelessWidget {
  final AppState state;
  const _EmptyState({required this.state});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final m = state.model(state.currentModelId);
    return Column(
      children: [
        const Spacer(flex: 2),
        // 中央大 logo（大写，颜色清晰）
        Text(
          'DARLING-AI',
          style: TextStyle(
            fontSize: 48,
            fontWeight: FontWeight.w700,
            letterSpacing: 3,
            color: dark ? const Color(0xFFB8BEC8) : const Color(0xFF8A9099),
          ),
        ),
        const SizedBox(height: 10),
        Text(
          '本地 AI 工作台',
          style: TextStyle(
            fontSize: 14,
            letterSpacing: 5,
            color: dark ? const Color(0xFF6A707C) : const Color(0xFF9AA1AC),
          ),
        ),
        const SizedBox(height: 44),
        _Composer(state: state),
        const Spacer(flex: 2),
        // 底部状态行（仿 opencode：Default Project / 无 Git）
        Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.folder_outlined, size: 14, color: cs.onSurfaceVariant),
              const SizedBox(width: 5),
              Text('Default Project',
                  style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              const SizedBox(width: 18),
              Icon(Icons.gite, size: 14, color: cs.onSurfaceVariant),
              const SizedBox(width: 5),
              Text('无 Git',
                  style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              if (m != null) ...[
                const SizedBox(width: 18),
                Icon(Icons.smart_toy_outlined, size: 14, color: cs.onSurfaceVariant),
                const SizedBox(width: 5),
                Text(m['display'] ?? m['id'] ?? '',
                    style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

// ==================== 消息列表 ====================
class _MessageList extends StatelessWidget {
  final AppState state;
  const _MessageList({required this.state});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: state,
      builder: (context, _) {
        final msgs = state.messages;
        // ask 确认弹窗
        if (state.askPending) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            _showAskDialog(context);
          });
        }
        if (msgs.isEmpty) {
          return const Center(
              child: Text('选择模型开始对话', style: TextStyle(color: Color(0xFF9AA1AC))));
        }
        return ListView.builder(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 14),
          itemCount: msgs.length + (state.busy ? 1 : 0),
          itemBuilder: (context, i) {
            if (i == msgs.length) {
              return ThinkingRow(text: state.thinkingText);
            }
            final msg = msgs[i];
            switch (msg.kind) {
              case MsgKind.user:
                return MessageBubble(role: 'user', text: msg.text);
              case MsgKind.ai:
                return MessageBubble(role: 'ai', text: msg.text);
              case MsgKind.tool:
                return ToolCard(title: msg.title, content: msg.text, ok: msg.ok);
              case MsgKind.error:
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Row(
                    children: [
                      Icon(Icons.error_outline,
                          size: 16,
                          color: Theme.of(context).colorScheme.error),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(msg.text,
                            style: TextStyle(
                                fontSize: 12.5,
                                color: Theme.of(context).colorScheme.error)),
                      ),
                    ],
                  ),
                );
              case MsgKind.think:
                return const SizedBox.shrink();
            }
          },
        );
      },
    );
  }

  Future<void> _showAskDialog(BuildContext context) async {
    final cmd = state.askCmd;
    if (!state.askPending) return;
    state.askPending = false; // 阻止重复弹窗
    final allow = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Text('执行命令确认'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('AI 请求在按需模式下执行以下命令：',
                style: TextStyle(
                    fontSize: 13,
                    color: Theme.of(ctx).colorScheme.onSurfaceVariant)),
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: Theme.of(ctx).brightness == Brightness.dark
                    ? const Color(0xFF21252D)
                    : const Color(0xFFFAFBFC),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                    color: Theme.of(ctx).brightness == Brightness.dark
                        ? kOutlineDark
                        : kOutlineLight),
              ),
              child: Text(cmd, style: const TextStyle(fontFamily: 'monospace', fontSize: 13)),
            ),
          ],
        ),
        actions: [
          OutlinedButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('拒绝')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('允许')),
        ],
      ),
    );
    await state.answerAsk(allow ?? false);
  }
}

// ==================== 输入区 ====================
class _Composer extends StatefulWidget {
  final AppState state;
  const _Composer({required this.state});

  @override
  State<_Composer> createState() => _ComposerState();
}

class _ComposerState extends State<_Composer> {
  final TextEditingController _ctl = TextEditingController();
  final List<Map<String, dynamic>> _images = [];
  final FocusNode _focus = FocusNode();

  @override
  void dispose() {
    _ctl.dispose();
    _focus.dispose();
    super.dispose();
  }

  Future<void> _pasteImage() async {
    // 桌面版剪贴板读取图片不可靠，优先走文件选择器
    try {
      final result = await Process.run('powershell', [
        '-NoProfile',
        '-Command',
        "Add-Type -AssemblyName System.Windows.Forms; "
            "\$d = New-Object System.Windows.Forms.OpenFileDialog; "
            "\$d.Multiselect = \$true; "
            "\$d.Filter = '图片|*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp'; "
            "if (\$d.ShowDialog() -eq 'OK') { \$d.FileNames -join '|' }"
      ]);
      final paths =
          (result.stdout as String).trim().split('|').where((p) => p.isNotEmpty);
      if (paths.isEmpty) return;
      for (final path in paths) {
        if (_images.length >= 4) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('最多支持 4 张图片')));
          break;
        }
        final f = File(path);
        if (!f.existsSync()) continue;
        final bytes = await f.readAsBytes();
        final ext = path.toLowerCase().split('.').last;
        final mime = ext == 'jpg' || ext == 'jpeg'
            ? 'image/jpeg'
            : ext == 'webp'
                ? 'image/webp'
                : ext == 'gif'
                    ? 'image/gif'
                    : 'image/png';
        setState(() {
          _images.add({
            'data_url': 'data:$mime;base64,${base64Encode(bytes)}'
          });
        });
      }
    } catch (_) {}
  }

  Future<void> _export() async {
    final md = await widget.state.exportConversation();
    if (md == null) return;
    // 用系统保存对话框
    final result = await Process.run('powershell', [
      '-NoProfile',
      '-Command',
      "Add-Type -AssemblyName System.Windows.Forms; "
          "\$d = New-Object System.Windows.Forms.SaveFileDialog; "
          "\$d.Filter = 'Markdown|*.md'; "
          "\$d.FileName = 'darling-ai-export.md'; "
          "if (\$d.ShowDialog() -eq 'OK') { \$d.FileName }"
    ]);
    final path = (result.stdout as String).trim();
    if (path.isEmpty) return;
    try {
      final f = File(path);
      if (!path.toLowerCase().endsWith('.md')) {
        await f.writeAsString('$md\n');
      } else {
        await f.writeAsString(md);
      }
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('已导出: $path')));
      }
    } catch (_) {}
  }

  void _send() {
    final text = _ctl.text;
    if (text.trim().isEmpty || widget.state.busy) return;
    final imgs = List<Map<String, dynamic>>.from(_images);
    _ctl.clear();
    _images.clear();
    widget.state.send(text, images: imgs);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final busy = widget.state.busy;
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 10, 20, 20),
      child: Center(
        child: Container(
          constraints: const BoxConstraints(maxWidth: 760),
          decoration: BoxDecoration(
            color: dark ? const Color(0xFF21252D) : const Color(0xFFFFFFFF),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
                color: dark ? kOutlineDark : const Color(0xFFE5E7EB), width: 1),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(0.06),
                blurRadius: 12,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // 已粘贴图片
              if (_images.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 10, 12, 0),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (var i = 0; i < _images.length; i++)
                          Stack(children: [
                            ClipRRect(
                              borderRadius: BorderRadius.circular(8),
                              child: Image.memory(
                                base64Decode(_images[i]['data_url']
                                    .toString()
                                    .split(',')
                                    .last),
                                width: 56,
                                height: 56,
                                fit: BoxFit.cover,
                              ),
                            ),
                            Positioned(
                              top: 2,
                              right: 2,
                              child: GestureDetector(
                                onTap: () => setState(() => _images.removeAt(i)),
                                child: Container(
                                  padding: const EdgeInsets.all(1),
                                  decoration: const BoxDecoration(
                                      color: Colors.black54,
                                      shape: BoxShape.circle),
                                  child: const Icon(Icons.close,
                                      size: 12, color: Colors.white),
                                ),
                              ),
                            ),
                          ]),
                      ],
                    ),
                  ),
                ),
              KeyboardListener(
                focusNode: _focus,
                autofocus: false,
                onKeyEvent: (ev) {
                  if (ev is KeyDownEvent &&
                      ev.logicalKey == LogicalKeyboardKey.keyV &&
                      HardwareKeyboard.instance.isControlPressed) {
                    _pasteImage();
                  }
                },
                child: TextField(
                  controller: _ctl,
                  focusNode: _focus,
                  minLines: 1,
                  maxLines: 6,
                  enabled: !busy,
                  onSubmitted: (_) => _send(),
                  style: TextStyle(fontSize: 14, color: cs.onSurface),
                  decoration: InputDecoration(
                    border: InputBorder.none,
                    hintText: busy ? 'AI 正在回复…' : '随便问点什么，/可查看命令，Ctrl+V 可粘贴图片...',
                    hintStyle:
                        TextStyle(fontSize: 14, color: cs.onSurfaceVariant),
                    contentPadding:
                        const EdgeInsets.fromLTRB(16, 14, 16, 10),
                  ),
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                child: Row(
                  children: [
                    // 导出当前会话
                    IconButton(
                      onPressed: busy ? null : _export,
                      tooltip: '导出会话 (Markdown)',
                      icon: Icon(Icons.ios_share,
                          size: 17, color: cs.onSurfaceVariant),
                      padding: const EdgeInsets.all(6),
                      constraints: const BoxConstraints(),
                    ),
                    // 当前模型（点击展开下拉面板，opencode 风格）
                    MenuAnchor(
                      alignmentOffset: const Offset(0, 8),
                      style: MenuStyle(
                        backgroundColor: WidgetStatePropertyAll(
                            dark ? const Color(0xFF1D2027) : const Color(0xFFFFFFFF)),
                        surfaceTintColor:
                            WidgetStatePropertyAll(Colors.transparent),
                        elevation: WidgetStatePropertyAll(10),
                        shadowColor: WidgetStatePropertyAll(Colors.black26),
                        side: WidgetStatePropertyAll(
                            BorderSide(color: cs.outlineVariant)),
                        shape: WidgetStatePropertyAll(
                            RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(14))),
                        padding: WidgetStatePropertyAll(EdgeInsets.zero),
                      ),
                      menuChildren: [
                        ModelPickerPanel(state: widget.state),
                      ],
                      builder: (context, controller, child) {
                        return InkWell(
                          onTap: () => controller.isOpen
                              ? controller.close()
                              : controller.open(),
                          borderRadius: BorderRadius.circular(6),
                          child: Padding(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 6, vertical: 4),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Text(
                                  widget.state.currentModelId == null
                                      ? '选择模型'
                                      : (widget.state.model(widget.state
                                              .currentModelId)?['display'] ??
                                          widget.state.currentModelId.toString()),
                                  style: TextStyle(
                                      fontSize: 13, color: cs.onSurface),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                                Icon(Icons.arrow_drop_down,
                                    size: 16, color: cs.onSurfaceVariant),
                              ],
                            ),
                          ),
                        );
                      },
                    ),
                    const Spacer(),
                    SizedBox(
                      width: 36,
                      height: 36,
                      child: FilledButton(
                        onPressed: busy ? null : _send,
                        style: FilledButton.styleFrom(
                          padding: EdgeInsets.zero,
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(10),
                          ),
                        ),
                        child: const Icon(Icons.arrow_upward, size: 18),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

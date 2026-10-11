import 'dart:io';

import 'package:flutter/material.dart';

import '../main.dart';
import '../state.dart';
import 'pages/models_page.dart';
import 'pages/plugins_page.dart';
import 'pages/settings_page.dart';
import 'pages/local_models_page.dart';
import 'pages/chat_page.dart';
import 'pages/browser_page.dart';

/// 经典模式（Zed 风格）：左侧历史对话栏 + 右侧对话区；
/// 设置 / 模型 / 插件 / 本地 以独立窗口（全屏浮层）打开。
class ClassicShell extends StatefulWidget {
  final AppState state;
  const ClassicShell({super.key, required this.state});

  @override
  State<ClassicShell> createState() => _ClassicShellState();
}

class _ClassicShellState extends State<ClassicShell> {
  bool _sidebarCollapsed = false;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final s = widget.state.settings;
    final bgImage = (s['bg_image'] ?? '').toString();
    final bgOpacity = (s['bg_opacity'] ?? 0.85).toDouble();
    return Scaffold(
      backgroundColor: dark ? const Color(0xFF15171C) : const Color(0xFFF4F5F7),
      body: Stack(
        fit: StackFit.expand,
        children: [
          if (bgImage.isNotEmpty && File(bgImage).existsSync())
            Opacity(opacity: bgOpacity, child: Image.file(File(bgImage), fit: BoxFit.cover)),
          Column(
            children: [
              _ClassicTopBar(state: widget.state),
              Expanded(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _ClassicSidebar(
                      state: widget.state,
                      collapsed: _sidebarCollapsed,
                      onToggle: () => setState(() => _sidebarCollapsed = !_sidebarCollapsed),
                    ),
                    Expanded(child: ChatPage(state: widget.state)),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ==================== 顶栏 ====================
class _ClassicTopBar extends StatelessWidget {
  final AppState state;
  const _ClassicTopBar({required this.state});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      height: 44,
      decoration: BoxDecoration(
        // 半透明：透出背景图片
        color: dark ? const Color(0xB31D2027) : const Color(0xB3FFFFFF),
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: Row(
        children: [
          const SizedBox(width: 12),
          Icon(Icons.auto_awesome, size: 17, color: cs.primary),
          const SizedBox(width: 8),
          Text('DARLING-AI',
              style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
          const SizedBox(width: 8),
          Text('v2.0',
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
          const Spacer(),
          // 模型切换 / 新建对话 已并入侧边栏，此处不留重复入口
          const SizedBox(width: 16),
        ],
      ),
    );
  }
}

// ==================== 侧边栏（抽屉式） ====================
class _ClassicSidebar extends StatelessWidget {
  final AppState state;
  final bool collapsed;
  final VoidCallback onToggle;
  const _ClassicSidebar(
      {required this.state, required this.collapsed, required this.onToggle});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final bg = dark ? const Color(0xB31D2027) : const Color(0xB3FAFBFC);
    return AnimatedContainer(
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      width: collapsed ? 30 : 230,
      decoration: BoxDecoration(
        // 半透明：透出背景图片
        color: bg,
        border: Border(right: BorderSide(color: cs.outlineVariant)),
      ),
      child: collapsed
          ? _CollapsedBar(onToggle: onToggle)
          : _SidebarContent(state: state, onToggle: onToggle),
    );
  }
}

// 收起态：只留一条窄把手
class _CollapsedBar extends StatelessWidget {
  final VoidCallback onToggle;
  const _CollapsedBar({required this.onToggle});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        IconButton(
          icon: Icon(Icons.chevron_right, size: 18, color: cs.onSurfaceVariant),
          onPressed: onToggle,
          tooltip: '展开侧边栏',
          visualDensity: VisualDensity.compact,
        ),
      ],
    );
  }
}

class _SidebarContent extends StatelessWidget {
  final AppState state;
  final VoidCallback onToggle;
  const _SidebarContent({required this.state, required this.onToggle});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // 对话标签
        Padding(
          padding: const EdgeInsets.fromLTRB(6, 10, 12, 6),
          child: Row(
            children: [
              IconButton(
                icon: Icon(Icons.chevron_left, size: 18, color: cs.onSurfaceVariant),
                onPressed: onToggle,
                tooltip: '收起侧边栏',
                visualDensity: VisualDensity.compact,
              ),
              const SizedBox(width: 2),
              Text('对话',
                  style: TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w700,
                      color: cs.onSurfaceVariant)),
              const Spacer(),
              IconButton(
                icon: Icon(Icons.add, size: 17, color: cs.onSurfaceVariant),
                onPressed: () => state.newConversation(),
                tooltip: '新对话',
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),
        ),
        // 历史对话列表
        Expanded(
          child: ListenableBuilder(
            listenable: state,
            builder: (context, _) {
              final convs = state.conversations;
              if (convs.isEmpty) {
                return const Center(
                    child: Text('暂无历史对话',
                        style: TextStyle(fontSize: 12, color: Color(0xFF9AA1AC))));
              }
              return ListView.builder(
                padding: const EdgeInsets.symmetric(horizontal: 6),
                itemCount: convs.length,
                itemBuilder: (context, i) {
                  final c = convs[i] as Map<String, dynamic>;
                  final id = c['id'] as String?;
                  final active = id == state.activeConvId;
                  return InkWell(
                    onTap: () => state.loadConversation(id!),
                    borderRadius: BorderRadius.circular(8),
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                      decoration: BoxDecoration(
                        color: active
                            ? (dark ? const Color(0xFF163A36) : const Color(0xFFE2F3F1))
                            : Colors.transparent,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Row(
                        children: [
                          Icon(Icons.forum_outlined,
                              size: 15,
                              color: active ? cs.primary : cs.onSurfaceVariant),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              (c['title'] ?? '未命名').toString(),
                              style: TextStyle(
                                fontSize: 13,
                                color: active ? cs.primary : cs.onSurface,
                                fontWeight: active ? FontWeight.w600 : FontWeight.w400,
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                },
              );
            },
          ),
        ),
        // 底部入口：设置 / 模型 / 插件 / 本地（独立窗口）
        Container(
          decoration: BoxDecoration(
            border: Border(top: BorderSide(color: cs.outlineVariant)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _SidebarEntry(
                icon: Icons.settings_outlined,
                label: '设置',
                onTap: () => _openWindow(context, '设置', SettingsPage(state: state)),
              ),
              _SidebarEntry(
                icon: Icons.model_training_outlined,
                label: '模型提供商',
                onTap: () => _openWindow(context, '模型提供商', ModelsPage(state: state)),
              ),
              _SidebarEntry(
                icon: Icons.extension_outlined,
                label: '插件',
                onTap: () => _openWindow(context, '插件', PluginsPage(state: state)),
              ),
              _SidebarEntry(
                icon: Icons.smart_toy_outlined,
                label: '本地模型',
                onTap: () => _openWindow(context, '本地模型', LocalModelsPage(state: state)),
              ),
              _SidebarEntry(
                icon: Icons.public_outlined,
                label: '浏览器',
                onTap: () => _openWindow(context, '浏览器', BrowserPage(state: state)),
              ),
            ],
          ),
        ),
      ],
    );
  }

  void _openWindow(BuildContext context, String title, Widget page) {
    showDialog(
      context: context,
      barrierDismissible: true,
      barrierColor: Colors.black54,
      builder: (_) => _WindowDialog(title: title, child: page),
    );
  }
}

class _SidebarEntry extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  const _SidebarEntry({required this.icon, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        child: Row(
          children: [
            Icon(icon, size: 16, color: cs.onSurfaceVariant),
            const SizedBox(width: 10),
            Text(label,
                style: TextStyle(fontSize: 13, color: cs.onSurface)),
            const Spacer(),
            Icon(Icons.open_in_new, size: 13, color: cs.outline),
          ],
        ),
      ),
    );
  }
}

// ==================== 独立窗口（全屏浮层，仿新窗口） ====================
class _WindowDialog extends StatelessWidget {
  final String title;
  final Widget child;
  const _WindowDialog({required this.title, required this.child});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 40, vertical: 36),
      backgroundColor: dark ? const Color(0xFF1D2027) : const Color(0xFFFAFBFC),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: cs.outlineVariant),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        children: [
          // 窗口标题栏
          Container(
            height: 42,
            padding: const EdgeInsets.symmetric(horizontal: 14),
            decoration: BoxDecoration(
              color: dark ? const Color(0xFF21252D) : const Color(0xFFF0F2F5),
              border: Border(bottom: BorderSide(color: cs.outlineVariant)),
            ),
            child: Row(
              children: [
                Icon(Icons.lens_blur, size: 15, color: cs.primary),
                const SizedBox(width: 8),
                Text(title,
                    style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
                const Spacer(),
                // 模拟窗口最小化/关闭
                InkWell(
                  onTap: () => Navigator.of(context).pop(),
                  borderRadius: BorderRadius.circular(6),
                  child: Container(
                    padding: const EdgeInsets.all(4),
                    child: Icon(Icons.close, size: 17, color: cs.onSurfaceVariant),
                  ),
                ),
              ],
            ),
          ),
          Expanded(child: child),
        ],
      ),
    );
  }
}

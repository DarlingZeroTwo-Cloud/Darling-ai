import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import 'bridge.dart';
import 'state.dart';
import 'classic_shell.dart';
import 'widgets/sidebar.dart';
import 'pages/chat_page.dart';
import 'pages/models_page.dart';
import 'pages/plugins_page.dart';
import 'pages/settings_page.dart';
import 'pages/local_models_page.dart';
import 'pages/browser_page.dart';

const Color kPrimaryLight = Color(0xFF0F766E);
const Color kPrimaryDark = Color(0xFF2BBBAD);
const Color kBgLight = Color(0xFFF4F5F7);
const Color kBgDark = Color(0xFF15171C);
const Color kSurfaceLight = Color(0xFFFFFFFF);
const Color kSurfaceDark = Color(0xFF1D2027);
const Color kOnSurfaceLight = Color(0xFF1B1E24);
const Color kOnSurfaceDark = Color(0xFFE6E8EE);
const Color kSubLight = Color(0xFF6A707C);
const Color kSubDark = Color(0xFF9AA2AF);
const Color kOutlineLight = Color(0xFFD6DAE1);
const Color kOutlineDark = Color(0xFF3A3F4A);
const Color kSelLight = Color(0xFFE2F3F1);
const Color kSelDark = Color(0xFF163A36);
const Color kErrLight = Color(0xFFD64545);
const Color kErrDark = Color(0xFFF07178);
const Color kOkLight = Color(0xFF2F9E6E);
const Color kOkDark = Color(0xFF5EC798);
const Color kHoverLight = Color(0xFFF0F2F5);
const Color kHoverDark = Color(0xFF262A33);
const Color kMeBubbleLight = Color(0xFFEEF0F4);
const Color kMeBubbleDark = Color(0xFF2A2E37);

/// 跟随系统的深浅主题（MD3 + 高对比，色值映射自 Qt 版）
ThemeData buildTheme(Brightness b) {
  final dark = b == Brightness.dark;
  final seed = dark ? kPrimaryDark : kPrimaryLight;
  var scheme = ColorScheme.fromSeed(seedColor: seed, brightness: b);
  scheme = scheme.copyWith(
    primary: dark ? kPrimaryDark : kPrimaryLight,
    onPrimary: Colors.white,
    surface: dark ? kSurfaceDark : kSurfaceLight,
    onSurface: dark ? kOnSurfaceDark : kOnSurfaceLight,
    onSurfaceVariant: dark ? kSubDark : kSubLight,
    outline: dark ? kOutlineDark : kOutlineLight,
    outlineVariant: dark ? const Color(0xFF343A45) : const Color(0xFFE4E6EA),
    secondaryContainer: dark ? kSelDark : kSelLight,
    onSecondaryContainer: dark ? kPrimaryDark : kPrimaryLight,
    error: dark ? kErrDark : kErrLight,
    surfaceContainerLowest: dark ? kBgDark : kBgLight,
    surfaceContainerLow: dark ? const Color(0xFF191C22) : const Color(0xFFF9FAFB),
    surfaceContainer: dark ? const Color(0xFF21252D) : const Color(0xFFFAFBFC),
    surfaceContainerHigh: dark ? const Color(0xFF262A33) : const Color(0xFFF0F2F5),
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    // 液态玻璃：全局半透明，透出 Windows 亚克力模糊
    scaffoldBackgroundColor: dark
        ? const Color(0xB31A1D24)
        : const Color(0xE6F2F4F8),
    canvasColor: dark
        ? const Color(0xB31A1D24)
        : const Color(0xE6F2F4F8),
    dividerColor: dark ? kOutlineDark : kOutlineLight,
    fontFamilyFallback: const ['Microsoft YaHei', 'PingFang SC', 'Segoe UI', 'Noto Sans CJK SC'],
    cardTheme: CardThemeData(
      color: dark ? const Color(0x9923272F) : const Color(0x99FFFFFF),
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: dark ? const Color(0x40FFFFFF) : const Color(0x33000000)),
      ),
      margin: EdgeInsets.zero,
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: dark ? const Color(0xF023272F) : const Color(0xF5FFFFFF),
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: dark ? const Color(0x40FFFFFF) : const Color(0x33000000)),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: dark ? const Color(0x6621252D) : const Color(0x66FFFFFF),
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: BorderSide(color: dark ? kOutlineDark : kOutlineLight, width: 1),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: BorderSide(color: dark ? kOutlineDark : kOutlineLight, width: 1),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: BorderSide(
            color: dark ? kPrimaryDark : kPrimaryLight, width: 1.6),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: dark ? kSurfaceDark : kSurfaceLight,
      indicatorColor: dark ? kSelDark : kSelLight,
      height: 64,
      labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: dark ? const Color(0xFF2D313A) : const Color(0xFF2D313A),
    ),
  );
}

/// 启动前确保后端桥接服务在线（打包后 backend/darling-backend 与 exe 同级）
Future<void> ensureBackend() async {
  final api = BridgeClient();
  if (await api.ping()) return;

  // 定位后端可执行文件
  String? backend;
  final env = Platform.environment['DARLING_AI_BACKEND'];
  if (env != null && env.isNotEmpty && File(env).existsSync()) {
    backend = env;
  } else {
    final exeDir = File(Platform.resolvedExecutable).parent.path;
    final cands = [
      '$exeDir/backend/darling-backend.exe',
      '$exeDir/backend/darling-backend',
      '$exeDir/darling-backend.exe',
      '$exeDir/darling-backend',
    ];
    for (final c in cands) {
      if (File(c).existsSync()) {
        backend = c;
        break;
      }
    }
  }
  if (backend == null) {
    // 开发模式：未找到打包后端，依赖手动启动
    return;
  }
  try {
    Process.start(backend, [], mode: ProcessStartMode.detached);
  } catch (_) {}

  // 轮询等就绪（最长 15 秒）
  for (var i = 0; i < 50; i++) {
    await Future.delayed(const Duration(milliseconds: 300));
    if (await api.ping()) return;
  }
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await ensureBackend();
  final api = BridgeClient();
  final state = AppState(api);
  try {
    await state.load();
  } catch (_) {
    state.messages.add(ChatMsg(MsgKind.error, '无法连接本地桥接服务 (127.0.0.1:19730)。'));
  }
  runApp(DarlingApp(state: state));
}

class DarlingApp extends StatelessWidget {
  final AppState state;
  const DarlingApp({super.key, required this.state});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: state,
      builder: (context, _) {
        return MaterialApp(
          title: 'darling-ai',
          debugShowCheckedModeBanner: false,
          theme: buildTheme(Brightness.light),
          darkTheme: buildTheme(Brightness.dark),
          themeMode: state.themeMode,
          home: HomeShell(state: state),
        );
      },
    );
  }
}

class HomeShell extends StatefulWidget {
  final AppState state;
  const HomeShell({super.key, required this.state});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _nav = 0;

  @override
  Widget build(BuildContext context) {
    // 经典模式：Zed 风格侧边栏 + 独立窗口
    if (widget.state.uiMode == 'classic') {
      return ClassicShell(state: widget.state);
    }
    // 精简模式：现有顶部标签栏 + 页面切换
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
          // 背景层：自定义图片 or 纯色
          if (bgImage.isNotEmpty && File(bgImage).existsSync())
            Opacity(
              opacity: bgOpacity,
              child: Image.file(File(bgImage), fit: BoxFit.cover),
            ),
          Column(
            children: [
          // 顶部标签栏
          Container(
            height: 44,
            decoration: BoxDecoration(
              color: dark ? const Color(0xFF1D2027) : const Color(0xFFFFFFFF),
              border: Border(bottom: BorderSide(color: cs.outlineVariant)),
            ),
            child: Row(
              children: [
                const SizedBox(width: 2),
                // 汉堡菜单：所有页面入口都在这里（新建对话只在右侧 +）
                PopupMenuButton<int>(
                  tooltip: '菜单',
                  icon: Icon(Icons.menu, size: 20, color: cs.onSurfaceVariant),
                  onSelected: (i) => setState(() => _nav = i),
                  itemBuilder: (ctx) => [
                    PopupMenuItem(
                      value: 0,
                      child: Row(children: [
                        Icon(Icons.forum_outlined, size: 18, color: _nav == 0 ? cs.primary : cs.onSurfaceVariant),
                        const SizedBox(width: 10),
                        Text('对话', style: TextStyle(fontWeight: _nav == 0 ? FontWeight.w600 : FontWeight.w400)),
                      ]),
                    ),
                    PopupMenuItem(
                      value: 1,
                      child: Row(children: [
                        Icon(Icons.model_training_outlined, size: 18, color: _nav == 1 ? cs.primary : cs.onSurfaceVariant),
                        const SizedBox(width: 10),
                        Text('模型', style: TextStyle(fontWeight: _nav == 1 ? FontWeight.w600 : FontWeight.w400)),
                      ]),
                    ),
                    PopupMenuItem(
                      value: 2,
                      child: Row(children: [
                        Icon(Icons.extension_outlined, size: 18, color: _nav == 2 ? cs.primary : cs.onSurfaceVariant),
                        const SizedBox(width: 10),
                        Text('插件', style: TextStyle(fontWeight: _nav == 2 ? FontWeight.w600 : FontWeight.w400)),
                      ]),
                    ),
                    PopupMenuItem(
                      value: 5,
                      child: Row(children: [
                        Icon(Icons.public_outlined, size: 18, color: _nav == 5 ? cs.primary : cs.onSurfaceVariant),
                        const SizedBox(width: 10),
                        Text('浏览器', style: TextStyle(fontWeight: _nav == 5 ? FontWeight.w600 : FontWeight.w400)),
                      ]),
                    ),
                    PopupMenuItem(
                      value: 4,
                      child: Row(children: [
                        Icon(Icons.smart_toy_outlined, size: 18, color: _nav == 4 ? cs.primary : cs.onSurfaceVariant),
                        const SizedBox(width: 10),
                        Text('本地', style: TextStyle(fontWeight: _nav == 4 ? FontWeight.w600 : FontWeight.w400)),
                      ]),
                    ),
                    PopupMenuItem(
                      value: 3,
                      child: Row(children: [
                        Icon(Icons.settings_outlined, size: 18, color: _nav == 3 ? cs.primary : cs.onSurfaceVariant),
                        const SizedBox(width: 10),
                        Text('设置', style: TextStyle(fontWeight: _nav == 3 ? FontWeight.w600 : FontWeight.w400)),
                      ]),
                    ),
                  ],
                ),
                const SizedBox(width: 2),
                // 会话标签 + 新对话按钮：只在对话页显示
                if (_nav == 0) ...[
                  PopupMenuButton<String>(
                    tooltip: '历史对话',
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
                      decoration: BoxDecoration(
                        color: cs.surfaceContainerHigh,
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: cs.outlineVariant),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.edit_note, size: 15, color: cs.onSurfaceVariant),
                          const SizedBox(width: 6),
                          const Text('会话', style: TextStyle(fontSize: 12.5)),
                          const SizedBox(width: 4),
                          Icon(Icons.arrow_drop_down, size: 16, color: cs.onSurfaceVariant),
                        ],
                      ),
                    ),
                    itemBuilder: (ctx) => [
                      for (final raw in widget.state.conversations)
                        PopupMenuItem(
                          value: raw['id'] as String,
                          child: Text(raw['title'] ?? '未命名',
                              style: TextStyle(
                                fontWeight: raw['id'] == widget.state.activeConvId
                                    ? FontWeight.w700
                                    : FontWeight.w400,
                              )),
                        ),
                      if (widget.state.conversations.isEmpty)
                        const PopupMenuItem(enabled: false, child: Text('暂无历史对话')),
                    ],
                    onSelected: (cid) => widget.state.loadConversation(cid),
                  ),
                ],
                const Spacer(),
                if (_nav == 0)
                  IconButton(
                    icon: Icon(Icons.add, size: 20, color: cs.onSurfaceVariant),
                    onPressed: () => widget.state.newConversation(),
                    tooltip: '新对话',
                  ),
                const SizedBox(width: 4),
              ],
            ),
          ),
          Expanded(
            child: _nav == 0
                ? ChatPage(state: widget.state)
                : _nav == 1
                    ? ModelsPage(state: widget.state)
                    : _nav == 2
                        ? PluginsPage(state: widget.state)
                        : _nav == 5
                            ? BrowserPage(state: widget.state)
                            : _nav == 4
                                ? LocalModelsPage(state: widget.state)
                                : SettingsPage(state: widget.state),
          ),
        ],
          ),
        ],
      ),
    );
  }
}

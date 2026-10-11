import 'package:flutter/material.dart';

import '../state.dart';

/// 内置浏览器面板：通过后端抓取网页正文（无需额外组件，跨平台）
class BrowserPage extends StatefulWidget {
  final AppState state;
  const BrowserPage({super.key, required this.state});

  @override
  State<BrowserPage> createState() => _BrowserPageState();
}

class _BrowserPageState extends State<BrowserPage> {
  final TextEditingController _url = TextEditingController();
  String _title = '';
  String _content = '';
  bool _loading = false;
  String _error = '';

  Future<void> _open([String? url]) async {
    final u = (url ?? _url.text).trim();
    if (u.isEmpty) return;
    if (!u.startsWith('http://') && !u.startsWith('https://')) {
      _url.text = 'https://$u';
    }
    final target = _url.text.trim();
    setState(() {
      _loading = true;
      _error = '';
      _title = '';
      _content = '';
    });
    try {
      final r = await widget.state.api.browserOpen(target);
      if (!mounted) return;
      if (r['ok'] == true) {
        setState(() {
          _loading = false;
          _title = (r['title'] ?? '').toString();
          _content = (r['text'] ?? '').toString();
        });
      } else {
        setState(() {
          _loading = false;
          _error = (r['error'] ?? '打开失败').toString();
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = '请求失败: $e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('浏览器',
                  style: TextStyle(
                      fontSize: 20,
                      fontWeight: FontWeight.w700,
                      color: cs.onSurface)),
              const SizedBox(height: 2),
              Text('让 AI 联网浏览网页；也可直接在此打开页面',
                  style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 4, 24, 8),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _url,
                  onSubmitted: (_) => _open(),
                  decoration: InputDecoration(
                    isDense: true,
                    hintText: '输入网址，例如 example.com',
                    prefixIcon: const Icon(Icons.language, size: 18),
                    fillColor: dark
                        ? const Color(0x6621252D)
                        : const Color(0x66FFFFFF),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton.icon(
                onPressed: _loading ? null : () => _open(),
                icon: _loading
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.open_in_new, size: 16),
                label: const Text('打开'),
              ),
            ],
          ),
        ),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(24, 4, 24, 20),
            child: Container(
              width: double.infinity,
              decoration: BoxDecoration(
                color: dark
                    ? const Color(0x9923272F)
                    : const Color(0x99FFFFFF),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: cs.outlineVariant),
              ),
              child: _error.isNotEmpty
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.all(20),
                        child: Text(
                          _error,
                          style: TextStyle(color: cs.error, fontSize: 14),
                        ),
                      ),
                    )
                  : _loading
                      ? const Center(child: CircularProgressIndicator())
                      : _content.isEmpty
                          ? Center(
                              child: Text(
                                '打开一个网页后，正文会显示在这里',
                                style: TextStyle(
                                    fontSize: 13, color: cs.onSurfaceVariant),
                              ),
                            )
                          : SingleChildScrollView(
                              padding: const EdgeInsets.all(20),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  if (_title.isNotEmpty)
                                    Padding(
                                      padding: const EdgeInsets.only(bottom: 12),
                                      child: Text(_title,
                                          style: const TextStyle(
                                              fontSize: 16,
                                              fontWeight: FontWeight.w700)),
                                    ),
                                  SelectableText(
                                    _content,
                                    style: const TextStyle(fontSize: 13.5),
                                  ),
                                ],
                              ),
                            ),
            ),
          ),
        ),
      ],
    );
  }
}

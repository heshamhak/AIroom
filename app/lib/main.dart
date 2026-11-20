import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'models/airoom_models.dart';
import 'services/airoom_api.dart';

void main() {
  runApp(const AiroomApp());
}

class AiroomApp extends StatelessWidget {
  const AiroomApp({super.key});

  @override
  Widget build(BuildContext context) {
    const primaryBlue = Color(0xFF2563EB);
    const inkBlack = Color(0xFF0F172A);

    return MaterialApp(
      title: 'AIroom',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.light,
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: primaryBlue,
          primary: primaryBlue,
          brightness: Brightness.light,
        ),
        scaffoldBackgroundColor: const Color(0xFFF1F5F9),
        textTheme: const TextTheme(
          bodyMedium: TextStyle(color: inkBlack),
          bodyLarge: TextStyle(color: inkBlack),
          titleMedium: TextStyle(color: inkBlack, fontWeight: FontWeight.bold),
        ),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
            backgroundColor: primaryBlue,
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(10),
            ),
          ),
        ),
      ),
      builder: (context, child) {
        return Directionality(
          textDirection: TextDirection.rtl,
          child: child ?? const SizedBox.shrink(),
        );
      },
      home: const AiroomHomePage(),
    );
  }
}

class AiroomHomePage extends StatefulWidget {
  const AiroomHomePage({super.key});

  @override
  State<AiroomHomePage> createState() => _AiroomHomePageState();
}

class _AiroomHomePageState extends State<AiroomHomePage> {
  final AiroomApi _api = AiroomApi(
    baseUrl: const String.fromEnvironment(
      'AIROOM_API_URL',
      defaultValue: 'http://127.0.0.1:8765',
    ),
  );
  final TextEditingController _titleCtrl = TextEditingController();
  final TextEditingController _requestCtrl = TextEditingController();
  final TextEditingController _workspaceCtrl =
      TextEditingController(text: '.');

  bool _backendOnline = false;
  bool _busy = false;
  bool _showAdvanced = false;
  String _mode = 'live_cdp';
  int _maxRounds = 4;

  CdpStatusModel? _cdpStatus;
  final String _cdpUrl = 'http://127.0.0.1:9223';

  List<SessionModel> _sessions = [];
  List<Map<String, dynamic>> _templates = [];
  SessionModel? _activeSession;
  WebSocketChannel? _wsChannel;
  StreamSubscription? _wsSub;
  Timer? _reconnectTimer;
  int _lastSeq = 0;
  int _backoffSeconds = 1;

  @override
  void initState() {
    super.initState();
    _initAndRefresh();
  }

  @override
  void dispose() {
    _reconnectTimer?.cancel();
    _wsSub?.cancel();
    _wsChannel?.sink.close();
    _titleCtrl.dispose();
    _requestCtrl.dispose();
    _workspaceCtrl.dispose();
    super.dispose();
  }

  Future<void> _initAndRefresh() async {
    await _api.ensureLocalToken();
    await _refreshAll();
  }

  Future<void> _refreshAll() async {
    final ok = await _api.checkHealth();
    if (!mounted) return;
    setState(() => _backendOnline = ok);
    if (ok) {
      await Future.wait([_checkCdp(), _loadSessions(), _loadTemplates()]);
    }
  }

  Future<void> _loadTemplates() async {
    try {
      final list = await _api.listTemplates();
      if (mounted) setState(() => _templates = list);
    } catch (_) {}
  }

  Future<void> _checkCdp() async {
    try {
      final status = await _api.getCdpStatus(_cdpUrl);
      if (mounted) setState(() => _cdpStatus = status);
    } catch (_) {}
  }

  Future<void> _loadSessions() async {
    try {
      final list = await _api.listSessions();
      if (!mounted) return;
      setState(() {
        _sessions = list;
        if (_activeSession == null && list.isNotEmpty) {
          _selectSession(list.first);
        }
      });
    } catch (_) {}
  }

  void _selectSession(SessionModel session) {
    _reconnectTimer?.cancel();
    _wsSub?.cancel();
    _wsChannel?.sink.close();
    _backoffSeconds = 1;
    _lastSeq = session.messages.isNotEmpty
        ? session.messages.map((m) => m.seq).reduce((a, b) => a > b ? a : b)
        : 0;
    setState(() => _activeSession = session);
    _openSessionWs(session.id, lastSeq: null);
  }

  void _openSessionWs(String sessionId, {int? lastSeq}) {
    _wsSub?.cancel();
    _wsChannel?.sink.close();
    try {
      _wsChannel = _api.connectSessionWs(sessionId, lastSeq: lastSeq);
      _wsSub = _wsChannel!.stream.listen(
        (raw) {
          final map = jsonDecode(raw as String) as Map<String, dynamic>;
          _backoffSeconds = 1;
          if (map['session'] != null && mounted) {
            final updated =
                SessionModel.fromJson(map['session'] as Map<String, dynamic>);
            if (updated.messages.isNotEmpty) {
              _lastSeq = updated.messages
                  .map((m) => m.seq)
                  .reduce((a, b) => a > b ? a : b);
            }
            setState(() {
              _activeSession = updated;
              final idx = _sessions.indexWhere((s) => s.id == updated.id);
              if (idx >= 0) {
                _sessions[idx] = updated;
              } else {
                _sessions.insert(0, updated);
              }
            });
          }
        },
        onDone: () => _scheduleWsReconnect(sessionId),
        onError: (_) => _scheduleWsReconnect(sessionId),
      );
    } catch (_) {
      _scheduleWsReconnect(sessionId);
    }
  }

  void _scheduleWsReconnect(String sessionId) {
    if (!mounted || _activeSession?.id != sessionId) return;
    _reconnectTimer?.cancel();
    final delay = Duration(seconds: _backoffSeconds);
    _backoffSeconds = (_backoffSeconds * 2).clamp(1, 30);
    _reconnectTimer = Timer(delay, () {
      if (mounted && _activeSession?.id == sessionId) {
        _openSessionWs(sessionId, lastSeq: _lastSeq);
      }
    });
  }

  Future<void> _launchBrowser() async {
    setState(() => _busy = true);
    try {
      await _api.launchChromeCdp(port: 9223);
      await _checkCdp();
      _showMsg('تم تشغيل متصفح AIroom المستقل على المنفذ 9223');
    } catch (e) {
      _showMsg('تعذر تشغيل المتصفح: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _createAndStart() async {
    final title = _titleCtrl.text.trim();
    final req = _requestCtrl.text.trim();
    if (title.isEmpty || req.isEmpty) {
      _showMsg('اكتب اسم المشروع والفكرة أولاً');
      return;
    }
    setState(() => _busy = true);
    try {
      final session = await _api.createSession(
        title: title,
        userRequest: req,
        targetWorkspace: _workspaceCtrl.text.trim().isEmpty
            ? '.'
            : _workspaceCtrl.text.trim(),
        cdpUrl: _cdpUrl,
        mode: _mode,
        maxRounds: _maxRounds,
      );
      _titleCtrl.clear();
      _requestCtrl.clear();
      _selectSession(session);
      await _api.startSession(session.id);
    } catch (e) {
      _showMsg('خطأ: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _prepareAndCopyForExecutor(SessionModel s) async {
    setState(() => _busy = true);
    try {
      final updated = await _api.exportSession(s.id);
      await _api.executeHandoff(s.id);
      if (updated.executorPrompt.isNotEmpty) {
        await Clipboard.setData(ClipboardData(text: updated.executorPrompt));
      }
      _showMsg('تم الاعتماد ونسخ أمر التنفيذ لـ Antigravity إلى الحافظة');
    } catch (e) {
      _showMsg('تعذر التجهيز: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _openSettingsDialog() async {
    final urlCtrl = TextEditingController(text: _api.baseUrl);
    final tokenCtrl = TextEditingController(text: _api.apiToken);
    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Colors.white,
        title: const Text('إعدادات الاتصال',
            style: TextStyle(color: Color(0xFF0F172A))),
        content: SizedBox(
          width: 400,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: urlCtrl,
                decoration: const InputDecoration(
                  labelText: 'عنوان الخادم',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: tokenCtrl,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'رمز المصادقة (API Token)',
                  border: OutlineInputBorder(),
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('إلغاء')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('حفظ')),
        ],
      ),
    );
    if (saved == true) {
      setState(() {
        _api.baseUrl = urlCtrl.text.trim().isEmpty
            ? 'http://127.0.0.1:8765'
            : urlCtrl.text.trim();
        _api.apiToken = tokenCtrl.text.trim();
      });
      await _refreshAll();
    }
  }

  void _showMsg(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    final isWide = MediaQuery.of(context).size.width >= 950;
    final browserOk = (_cdpStatus?.connected ?? false) &&
        (_cdpStatus?.chatgptReady ?? false) &&
        (_cdpStatus?.claudeReady ?? false);

    return Scaffold(
      appBar: AppBar(
        backgroundColor: Colors.white.withValues(alpha: 0.85),
        foregroundColor: const Color(0xFF0F172A),
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(1),
          child: Container(height: 1, color: const Color(0xFFE2E8F0)),
        ),
        title: const Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.hub, color: Color(0xFF2563EB)),
            SizedBox(width: 8),
            Flexible(
              child: Text(
                'AIroom',
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: Color(0xFF0F172A),
                ),
              ),
            ),
          ],
        ),
        actions: [
          _SimplePill(
              label: isWide
                  ? (_backendOnline ? 'الخادم متصل' : 'الخادم متوقف')
                  : 'الخادم',
              ok: _backendOnline),
          const SizedBox(width: 6),
          _SimplePill(
              label: isWide
                  ? (browserOk ? 'المتصفح جاهز' : 'المتصفح متوقف')
                  : 'المتصفح',
              ok: browserOk),
          const SizedBox(width: 6),
          if (!browserOk && isWide)
            FilledButton.icon(
              onPressed: _busy ? null : _launchBrowser,
              icon: const Icon(Icons.open_in_browser, size: 18),
              label: const Text('تشغيل المتصفح'),
            )
          else if (!browserOk)
            IconButton(
              tooltip: 'تشغيل المتصفح',
              color: const Color(0xFF2563EB),
              onPressed: _busy ? null : _launchBrowser,
              icon: const Icon(Icons.open_in_browser),
            ),
          IconButton(
            tooltip: 'تحديث',
            onPressed: _refreshAll,
            icon: const Icon(Icons.refresh, color: Color(0xFF0F172A)),
          ),
          IconButton(
            tooltip: 'الإعدادات',
            onPressed: _openSettingsDialog,
            icon: const Icon(Icons.settings, color: Color(0xFF0F172A)),
          ),
        ],
      ),
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topRight,
            end: Alignment.bottomLeft,
            colors: [
              Color(0xFFF8FAFC),
              Color(0xFFEFF6FF),
              Color(0xFFF1F5F9),
            ],
          ),
        ),
        child: isWide
            ? Row(
                children: [
                  SizedBox(width: 340, child: _buildLeftSetupAndList()),
                  const VerticalDivider(width: 1, color: Color(0xFFE2E8F0)),
                  Expanded(child: _buildRelayArea()),
                ],
              )
            : DefaultTabController(
                length: 2,
                child: Column(
                  children: [
                    const TabBar(
                      labelColor: Color(0xFF2563EB),
                      unselectedLabelColor: Color(0xFF475569),
                      tabs: [
                        Tab(text: 'مشروع جديد والمشاريع'),
                        Tab(text: 'غرفة التداول'),
                      ],
                    ),
                    Expanded(
                      child: TabBarView(
                        children: [
                          _buildLeftSetupAndList(),
                          _buildRelayArea(),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
      ),
    );
  }

  Widget _buildLeftSetupAndList() {
    return Container(
      color: Colors.white.withValues(alpha: 0.72),
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text('ابدأ مشروعاً جديداً',
                    style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 15,
                        color: Color(0xFF0F172A))),
                if (_templates.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: _templates.map((tpl) {
                      return ActionChip(
                        label: Text(tpl['name']?.toString() ?? '',
                            style: const TextStyle(fontSize: 11)),
                        backgroundColor: Colors.white,
                        onPressed: () {
                          _titleCtrl.text = tpl['title_hint']?.toString() ?? '';
                          _requestCtrl.text = tpl['prompt']?.toString() ?? '';
                        },
                      );
                    }).toList(),
                  ),
                ],
                const SizedBox(height: 10),
                TextField(
                  controller: _titleCtrl,
                  style: const TextStyle(color: Color(0xFF0F172A)),
                  decoration: InputDecoration(
                    hintText: 'اسم المشروع',
                    filled: true,
                    fillColor: Colors.white.withValues(alpha: 0.9),
                    isDense: true,
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10)),
                  ),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: _requestCtrl,
                  maxLines: 3,
                  style: const TextStyle(color: Color(0xFF0F172A)),
                  decoration: InputDecoration(
                    hintText: 'اشرح فكرتك ببساطة...',
                    filled: true,
                    fillColor: Colors.white.withValues(alpha: 0.9),
                    isDense: true,
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10)),
                  ),
                ),
                const SizedBox(height: 6),
                InkWell(
                  onTap: () => setState(() => _showAdvanced = !_showAdvanced),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Row(
                      children: [
                        Icon(
                          _showAdvanced
                              ? Icons.keyboard_arrow_up
                              : Icons.keyboard_arrow_down,
                          size: 18,
                          color: const Color(0xFF475569),
                        ),
                        const SizedBox(width: 4),
                        const Text('خيارات إضافية',
                            style: TextStyle(
                                fontSize: 12, color: Color(0xFF475569))),
                      ],
                    ),
                  ),
                ),
                if (_showAdvanced) ...[
                  const SizedBox(height: 6),
                  TextField(
                    controller: _workspaceCtrl,
                    decoration: const InputDecoration(
                      labelText: 'مجلد العمل',
                      isDense: true,
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      Expanded(
                        child: DropdownButtonFormField<String>(
                          initialValue: _mode,
                          isDense: true,
                          decoration: const InputDecoration(
                              border: OutlineInputBorder()),
                          items: const [
                            DropdownMenuItem(
                                value: 'live_cdp',
                                child: Text('المتصفح المدمج')),
                            DropdownMenuItem(
                                value: 'simulation', child: Text('محاكاة')),
                          ],
                          onChanged: (v) =>
                              setState(() => _mode = v ?? 'live_cdp'),
                        ),
                      ),
                      const SizedBox(width: 8),
                      DropdownButton<int>(
                        value: _maxRounds,
                        items: [2, 3, 4, 6]
                            .map((n) => DropdownMenuItem(
                                value: n, child: Text('$n جولات')))
                            .toList(),
                        onChanged: (v) => setState(() => _maxRounds = v ?? 4),
                      ),
                    ],
                  ),
                ],
                const SizedBox(height: 10),
                FilledButton.icon(
                  onPressed:
                      (_backendOnline && !_busy) ? _createAndStart : null,
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('ابدأ التخطيط الآن'),
                ),
              ],
            ),
          ),
          const Divider(height: 1, color: Color(0xFFE2E8F0)),
          Expanded(
            child: _sessions.isEmpty
                ? const Center(
                    child: Text('لا توجد مشاريع بعد',
                        style: TextStyle(color: Color(0xFF64748B))),
                  )
                : Material(
                    type: MaterialType.transparency,
                    child: ListView.builder(
                      itemCount: _sessions.length,
                      itemBuilder: (ctx, idx) {
                        final s = _sessions[idx];
                        final selected = _activeSession?.id == s.id;
                        return ListTile(
                          selected: selected,
                          selectedTileColor:
                              const Color(0xFF2563EB).withValues(alpha: 0.1),
                          title: Text(s.title,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                  color: Color(0xFF0F172A),
                                  fontWeight: FontWeight.w600)),
                          subtitle: Text(_stageLabel(s.stage),
                              style: const TextStyle(
                                  fontSize: 12, color: Color(0xFF475569))),
                          trailing: Text('${s.currentRound}/${s.maxRounds}',
                              style: const TextStyle(
                                  fontSize: 12, color: Color(0xFF64748B))),
                          onTap: () => _selectSession(s),
                        );
                      },
                    ),
                  ),
          ),
        ],
      ),
    );
  }

  Widget _buildRelayArea() {
    final s = _activeSession;
    if (s == null) {
      return const Center(
        child: Text(
          'اكتب فكرتك واضغط "ابدأ التخطيط الآن"، أو افتح تبويب المتصفح المدمج لتسجيل الدخول.',
          style: TextStyle(color: Color(0xFF475569), fontSize: 15),
        ),
      );
    }

    final approvedCount =
        s.planItems.where((i) => i.status == 'APPROVED').length;
    final total = s.planItems.length;
    final allApproved = total > 0 && approvedCount == total;

    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          color: Colors.white.withValues(alpha: 0.78),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(s.title,
                        style: const TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                            color: Color(0xFF0F172A))),
                    const SizedBox(height: 4),
                    Text(
                      '${_stageLabel(s.stage)} • الموافقات: $approvedCount / $total',
                      style: const TextStyle(
                          fontSize: 12, color: Color(0xFF475569)),
                    ),
                  ],
                ),
              ),
              if (s.stage == 'paused' || s.stage == 'idle')
                FilledButton.icon(
                  onPressed: () => _api.startSession(s.id),
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('استكمال'),
                )
              else if (s.stage == 'planning_chatgpt' ||
                  s.stage == 'validating_claude' ||
                  s.stage == 'revising_chatgpt')
                OutlinedButton.icon(
                  onPressed: () => _api.pauseSession(s.id),
                  icon: const Icon(Icons.pause),
                  label: const Text('إيقاف'),
                ),
              const SizedBox(width: 8),
              if (allApproved) ...[
                FilledButton.icon(
                  onPressed: _busy ? null : () => _prepareAndCopyForExecutor(s),
                  icon: const Icon(Icons.check_circle),
                  label: const Text('نسخ للمنفّذ (Antigravity)'),
                ),
                const SizedBox(width: 6),
                if (s.finalPlanMarkdown.isNotEmpty)
                  OutlinedButton.icon(
                    onPressed: () {
                      showDialog(
                        context: context,
                        builder: (ctx) => AlertDialog(
                          backgroundColor: Colors.white,
                          title: Text('الخطة المعتمدة: ${s.title}',
                              style: const TextStyle(color: Color(0xFF0F172A))),
                          content: SizedBox(
                            width: 620,
                            height: 420,
                            child: SingleChildScrollView(
                              child: SelectableText(s.finalPlanMarkdown,
                                  style: const TextStyle(
                                      fontSize: 13, color: Color(0xFF0F172A))),
                            ),
                          ),
                          actions: [
                            TextButton(
                              onPressed: () => Navigator.pop(ctx),
                              child: const Text('إغلاق'),
                            ),
                          ],
                        ),
                      );
                    },
                    icon: const Icon(Icons.description_outlined,
                        color: Color(0xFF2563EB)),
                    label: const Text('عرض الخطة'),
                  ),
                const SizedBox(width: 6),
                OutlinedButton.icon(
                  onPressed: _busy
                      ? null
                      : () async {
                          setState(() => _busy = true);
                          try {
                            final res = await _api.runVerifyLoop(
                                s.id, './scripts/validate_release.sh');
                            _showMsg(res['ok'] == true
                                ? 'تم التحقق والاختبار بنجاح ✓'
                                : 'فشل التحقق — تم تجهيز رسالة الإصلاح الذاتي');
                          } catch (e) {
                            _showMsg('خطأ التحقق: $e');
                          } finally {
                            if (mounted) setState(() => _busy = false);
                          }
                        },
                  icon: const Icon(Icons.verified, color: Color(0xFF2563EB)),
                  label: const Text('تحقق وإغلاق الحلقة'),
                ),
              ],
            ],
          ),
        ),
        if (s.planItems.isNotEmpty)
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            color: Colors.white.withValues(alpha: 0.6),
            child: Wrap(
              spacing: 8,
              runSpacing: 6,
              children: s.planItems.map((item) {
                final ok = item.status == 'APPROVED';
                return Chip(
                  avatar: Icon(
                    ok ? Icons.check_circle : Icons.hourglass_top,
                    size: 16,
                    color: ok ? const Color(0xFF16A34A) : const Color(0xFFD97706),
                  ),
                  label: Text('${item.id}: ${item.title}',
                      style: const TextStyle(
                          fontSize: 12, color: Color(0xFF0F172A))),
                  backgroundColor: Colors.white,
                );
              }).toList(),
            ),
          ),
        if (s.error != null)
          Container(
            width: double.infinity,
            color: Colors.red.withValues(alpha: 0.12),
            padding: const EdgeInsets.all(10),
            child: Text(s.error!,
                style:
                    const TextStyle(color: Color(0xFFB91C1C), fontSize: 12)),
          ),
        Expanded(
          child: ListView.builder(
            padding: const EdgeInsets.all(16),
            itemCount: s.messages.length,
            itemBuilder: (ctx, idx) {
              return _SimpleMessageBubble(message: s.messages[idx]);
            },
          ),
        ),
      ],
    );
  }

  String _stageLabel(String stage) {
    switch (stage) {
      case 'idle':
        return 'جاهز';
      case 'planning_chatgpt':
        return 'ChatGPT يخطط...';
      case 'validating_claude':
        return 'Claude يدقق...';
      case 'revising_chatgpt':
        return 'ChatGPT يعدّل...';
      case 'consensus_reached':
        return 'تم الاتفاق الكامل';
      case 'exported':
        return 'جاهز للتنفيذ';
      case 'verifying':
        return 'جاري تشغيل الاختبارات...';
      case 'verified_done':
        return 'تم التحقق والاختبار بنجاح ✓';
      case 'paused':
        return 'متوقف مؤقتاً';
      case 'failed':
        return 'فشل';
      default:
        return stage;
    }
  }
}

class _SimplePill extends StatelessWidget {
  final String label;
  final bool ok;
  const _SimplePill({required this.label, required this.ok});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: (ok ? const Color(0xFF16A34A) : const Color(0xFFDC2626))
            .withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w600,
          color: ok ? const Color(0xFF15803D) : const Color(0xFFB91C1C),
        ),
      ),
    );
  }
}

class _SimpleMessageBubble extends StatelessWidget {
  final RelayMessageModel message;
  const _SimpleMessageBubble({required this.message});

  @override
  Widget build(BuildContext context) {
    final senderLabel = switch (message.sender) {
      'USER' => 'أنت',
      'CHATGPT' => 'ChatGPT (المخطط)',
      'CLAUDE' => 'Claude (المدقق)',
      'EXECUTOR' => 'Antigravity (المنفّذ)',
      _ => 'الوسيط',
    };
    final color = switch (message.sender) {
      'CHATGPT' => const Color(0xFF059669),
      'CLAUDE' => const Color(0xFFD97706),
      'USER' => const Color(0xFF2563EB),
      _ => const Color(0xFF4F46E5),
    };

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.85),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(senderLabel,
                  style: TextStyle(
                      fontWeight: FontWeight.bold, color: color, fontSize: 13)),
              const Spacer(),
              Text('جولة ${message.roundNum}',
                  style:
                      const TextStyle(fontSize: 11, color: Color(0xFF64748B))),
            ],
          ),
          const SizedBox(height: 6),
          SelectableText(message.content,
              style: const TextStyle(
                  fontSize: 13, height: 1.45, color: Color(0xFF0F172A))),
        ],
      ),
    );
  }
}

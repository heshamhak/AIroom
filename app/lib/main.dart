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
        scaffoldBackgroundColor: const Color(0xFFF8FAFC),
        textTheme: const TextTheme(
          bodyMedium: TextStyle(color: inkBlack),
          bodyLarge: TextStyle(color: inkBlack),
          titleMedium: TextStyle(color: inkBlack, fontWeight: FontWeight.w700),
        ),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
            backgroundColor: primaryBlue,
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
          ),
        ),
        outlinedButtonTheme: OutlinedButtonThemeData(
          style: OutlinedButton.styleFrom(
            foregroundColor: primaryBlue,
            side: const BorderSide(color: Color(0xFFCBD5E1)),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
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
  final TextEditingController _workspaceCtrl = TextEditingController(text: '.');

  bool _backendOnline = false;
  bool _busy = false;
  bool _creatingNew = true;
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
      setState(() => _sessions = list);
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
    setState(() {
      _activeSession = session;
      _creatingNew = false;
    });
    _openSessionWs(session.id, lastSeq: null);
  }

  void _openSessionWs(String sessionId, {int? lastSeq}) {
    _wsSub?.cancel();
    _wsChannel?.sink.close();
    try {
      _wsChannel = _api.connectSessionWs(sessionId, lastSeq: lastSeq);
      _wsChannel!.ready.catchError((_) => _scheduleWsReconnect(sessionId));
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
      _showMsg('تم فتح متصفح AIroom');
    } catch (e) {
      _showMsg('تعذر فتح المتصفح: $e');
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
      _showMsg('تم نسخ الخطة المعتمدة لـ Antigravity إلى الحافظة');
    } catch (e) {
      _showMsg('تعذر النسخ: $e');
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
        title: const Text('إعدادات الاتصال'),
        content: SizedBox(
          width: 380,
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
    final isWide = MediaQuery.of(context).size.width >= 900;
    final browserOk = (_cdpStatus?.connected ?? false) &&
        (_cdpStatus?.chatgptReady ?? false) &&
        (_cdpStatus?.claudeReady ?? false);

    return Scaffold(
      appBar: AppBar(
        backgroundColor: Colors.white,
        foregroundColor: const Color(0xFF0F172A),
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(1),
          child: Container(height: 1, color: const Color(0xFFE2E8F0)),
        ),
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              padding: const EdgeInsets.all(6),
              decoration: BoxDecoration(
                color: const Color(0xFF2563EB).withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(8),
              ),
              child: const Icon(Icons.hub, color: Color(0xFF2563EB), size: 20),
            ),
            const SizedBox(width: 10),
            const Text('AIroom',
                style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
            const SizedBox(width: 10),
            _DotStatus(ok: _backendOnline, label: _backendOnline ? 'متصل' : 'غير متصل'),
          ],
        ),
        actions: [
          if (!browserOk)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: FilledButton.icon(
                onPressed: _busy ? null : _launchBrowser,
                icon: const Icon(Icons.open_in_browser, size: 18),
                label: Text(isWide ? 'فتح المتصفح' : 'المتصفح'),
              ),
            )
          else if (isWide)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 10),
              child: _DotStatus(ok: true, label: 'المتصفح جاهز'),
            ),
          IconButton(
            tooltip: 'الإعدادات',
            onPressed: _openSettingsDialog,
            icon: const Icon(Icons.settings_outlined),
          ),
          const SizedBox(width: 6),
        ],
      ),
      body: isWide
          ? Row(
              children: [
                SizedBox(width: 260, child: _buildSidebar()),
                const VerticalDivider(width: 1, color: Color(0xFFE2E8F0)),
                Expanded(child: _buildMainContent()),
              ],
            )
          : _buildMainContent(),
      drawer: isWide ? null : Drawer(child: SafeArea(child: _buildSidebar())),
    );
  }

  Widget _buildSidebar() {
    return Container(
      color: Colors.white,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.all(14),
            child: FilledButton.icon(
              onPressed: () {
                setState(() {
                  _creatingNew = true;
                  _activeSession = null;
                });
              },
              icon: const Icon(Icons.add),
              label: const Text('مشروع جديد'),
            ),
          ),
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Text('المشاريع السابقة',
                style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: Color(0xFF64748B))),
          ),
          Expanded(
            child: _sessions.isEmpty
                ? const Center(
                    child: Text('لا توجد مشاريع بعد',
                        style:
                            TextStyle(color: Color(0xFF94A3B8), fontSize: 13)),
                  )
                : Material(
                    type: MaterialType.transparency,
                    child: ListView.builder(
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                      itemCount: _sessions.length,
                      itemBuilder: (ctx, idx) {
                        final s = _sessions[idx];
                        final selected =
                            !_creatingNew && _activeSession?.id == s.id;
                        return Container(
                          margin: const EdgeInsets.only(bottom: 4),
                          decoration: BoxDecoration(
                            color: selected
                                ? const Color(0xFF2563EB).withValues(alpha: 0.08)
                                : Colors.transparent,
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: ListTile(
                            dense: true,
                            shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(10)),
                            title: Text(
                              s.title,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontWeight: selected
                                    ? FontWeight.w700
                                    : FontWeight.w500,
                                color: selected
                                    ? const Color(0xFF2563EB)
                                    : const Color(0xFF0F172A),
                              ),
                            ),
                            subtitle: Text(_stageLabel(s.stage),
                                style: const TextStyle(
                                    fontSize: 11, color: Color(0xFF64748B))),
                            onTap: () => _selectSession(s),
                          ),
                        );
                      },
                    ),
                  ),
          ),
        ],
      ),
    );
  }

  Widget _buildMainContent() {
    if (_creatingNew || _activeSession == null) {
      return _buildNewProjectCard();
    }
    return _buildSessionWorkspace(_activeSession!);
  }

  Widget _buildNewProjectCard() {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 620),
          child: Container(
            padding: const EdgeInsets.all(28),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: const Color(0xFFE2E8F0)),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.04),
                  blurRadius: 24,
                  offset: const Offset(0, 8),
                ),
              ],
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'ما الذي تريد بناءه اليوم؟',
                  style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.bold,
                      color: Color(0xFF0F172A)),
                ),
                const SizedBox(height: 6),
                const Text(
                  'سيقوم ChatGPT بالتخطيط وClaude بالتدقيق والموافقة تلقائياً.',
                  style: TextStyle(fontSize: 13, color: Color(0xFF64748B)),
                ),
                if (_templates.isNotEmpty) ...[
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: _templates.map((tpl) {
                      return ActionChip(
                        label: Text(tpl['name']?.toString() ?? '',
                            style: const TextStyle(fontSize: 12)),
                        backgroundColor: const Color(0xFFF8FAFC),
                        side: const BorderSide(color: Color(0xFFE2E8F0)),
                        onPressed: () {
                          _titleCtrl.text =
                              tpl['title_hint']?.toString() ?? '';
                          _requestCtrl.text = tpl['prompt']?.toString() ?? '';
                        },
                      );
                    }).toList(),
                  ),
                ],
                const SizedBox(height: 18),
                TextField(
                  controller: _titleCtrl,
                  decoration: InputDecoration(
                    labelText: 'اسم المشروع',
                    hintText: 'مثال: تطبيق إدارة المهام',
                    filled: true,
                    fillColor: const Color(0xFFF8FAFC),
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12)),
                  ),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: _requestCtrl,
                  maxLines: 4,
                  decoration: InputDecoration(
                    labelText: 'فكرة المشروع ومتطلباته',
                    hintText: 'اكتب وصفاً واضحاً لما تريد تنفيذه...',
                    filled: true,
                    fillColor: const Color(0xFFF8FAFC),
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12)),
                  ),
                ),
                const SizedBox(height: 10),
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                    onPressed: () =>
                        setState(() => _showAdvanced = !_showAdvanced),
                    icon: Icon(
                      _showAdvanced
                          ? Icons.keyboard_arrow_up
                          : Icons.tune,
                      size: 16,
                    ),
                    label: const Text('خيارات متقدمة',
                        style: TextStyle(fontSize: 12)),
                  ),
                ),
                if (_showAdvanced) ...[
                  const SizedBox(height: 8),
                  TextField(
                    controller: _workspaceCtrl,
                    decoration: InputDecoration(
                      labelText: 'مجلد المشروع',
                      isDense: true,
                      border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(10)),
                    ),
                  ),
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      Expanded(
                        child: DropdownButtonFormField<String>(
                          initialValue: _mode,
                          isDense: true,
                          decoration: InputDecoration(
                            labelText: 'المحرك',
                            border: OutlineInputBorder(
                                borderRadius: BorderRadius.circular(10)),
                          ),
                          items: const [
                            DropdownMenuItem(
                                value: 'live_cdp',
                                child: Text('المتصفح (ChatGPT + Claude)')),
                            DropdownMenuItem(
                                value: 'ollama_local',
                                child: Text('Ollama محلي')),
                            DropdownMenuItem(
                                value: 'simulation', child: Text('محاكاة')),
                          ],
                          onChanged: (v) =>
                              setState(() => _mode = v ?? 'live_cdp'),
                        ),
                      ),
                      const SizedBox(width: 12),
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
                const SizedBox(height: 18),
                FilledButton.icon(
                  onPressed:
                      (_backendOnline && !_busy) ? _createAndStart : null,
                  icon: const Icon(Icons.play_arrow_rounded),
                  label: const Text('ابدأ التخطيط',
                      style:
                          TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildSessionWorkspace(SessionModel s) {
    final approvedCount =
        s.planItems.where((i) => i.status == 'APPROVED').length;
    final total = s.planItems.length;
    final allApproved = total > 0 && approvedCount == total;
    final isRunning = s.stage == 'planning_chatgpt' ||
        s.stage == 'validating_claude' ||
        s.stage == 'revising_chatgpt' ||
        s.stage == 'verifying';

    return Column(
      children: [
        // Clean Step Progress Header
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
          color: Colors.white,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(s.title,
                        style: const TextStyle(
                            fontSize: 18, fontWeight: FontWeight.bold)),
                  ),
                  _StepChip(
                    step: '1. التخطيط',
                    active: s.stage == 'planning_chatgpt' ||
                        s.stage == 'revising_chatgpt',
                    done: total > 0 && s.stage != 'planning_chatgpt',
                  ),
                  const SizedBox(width: 6),
                  _StepChip(
                    step: '2. التدقيق',
                    active: s.stage == 'validating_claude',
                    done: allApproved,
                  ),
                  const SizedBox(width: 6),
                  _StepChip(
                    step: '3. جاهز',
                    active: allApproved,
                    done: s.stage == 'verified_done',
                  ),
                ],
              ),
              if (total > 0) ...[
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  children: s.planItems.map((item) {
                    final ok = item.status == 'APPROVED';
                    return Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 5),
                      decoration: BoxDecoration(
                        color: ok
                            ? const Color(0xFF16A34A).withValues(alpha: 0.08)
                            : const Color(0xFFF59E0B).withValues(alpha: 0.08),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(
                          color: ok
                              ? const Color(0xFF86EFAC)
                              : const Color(0xFFFCD34D),
                        ),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            ok ? Icons.check_circle : Icons.schedule,
                            size: 14,
                            color: ok
                                ? const Color(0xFF16A34A)
                                : const Color(0xFFD97706),
                          ),
                          const SizedBox(width: 5),
                          Text('${item.id}: ${item.title}',
                              style: const TextStyle(fontSize: 12)),
                        ],
                      ),
                    );
                  }).toList(),
                ),
              ],
            ],
          ),
        ),
        const Divider(height: 1, color: Color(0xFFE2E8F0)),
        if (s.error != null)
          Container(
            width: double.infinity,
            color: const Color(0xFFFEF2F2),
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
            child: Text(s.error!,
                style:
                    const TextStyle(color: Color(0xFFB91C1C), fontSize: 12)),
          ),
        // Clean Message Feed
        Expanded(
          child: ListView.builder(
            padding: const EdgeInsets.all(20),
            itemCount: s.messages.length,
            itemBuilder: (ctx, idx) =>
                _SimpleMessageBubble(message: s.messages[idx]),
          ),
        ),
        // Bottom Clear Action Bar
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
          decoration: const BoxDecoration(
            color: Colors.white,
            border: Border(top: BorderSide(color: Color(0xFFE2E8F0))),
          ),
          child: Row(
            children: [
              Text(
                _stageLabel(s.stage),
                style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: Color(0xFF475569)),
              ),
              const Spacer(),
              if (isRunning)
                OutlinedButton.icon(
                  onPressed: () => _api.pauseSession(s.id),
                  icon: const Icon(Icons.pause, size: 18),
                  label: const Text('إيقاف مؤقت'),
                )
              else if (s.stage == 'paused' ||
                  s.stage == 'idle' ||
                  s.stage == 'failed')
                FilledButton.icon(
                  onPressed: () => _api.startSession(s.id),
                  icon: const Icon(Icons.play_arrow, size: 18),
                  label: const Text('استكمال'),
                ),
              if (allApproved) ...[
                if (s.finalPlanMarkdown.isNotEmpty) ...[
                  OutlinedButton(
                    onPressed: () {
                      showDialog(
                        context: context,
                        builder: (ctx) => AlertDialog(
                          backgroundColor: Colors.white,
                          title: Text('الخطة المعتمدة: ${s.title}'),
                          content: SizedBox(
                            width: 600,
                            height: 400,
                            child: SingleChildScrollView(
                              child: SelectableText(s.finalPlanMarkdown,
                                  style: const TextStyle(fontSize: 13)),
                            ),
                          ),
                          actions: [
                            TextButton(
                                onPressed: () => Navigator.pop(ctx),
                                child: const Text('إغلاق')),
                          ],
                        ),
                      );
                    },
                    child: const Text('عرض الخطة'),
                  ),
                  const SizedBox(width: 8),
                ],
                OutlinedButton(
                  onPressed: _busy
                      ? null
                      : () async {
                          setState(() => _busy = true);
                          try {
                            final res = await _api.runVerifyLoop(
                                s.id, './scripts/validate_release.sh');
                            _showMsg(res['ok'] == true
                                ? 'نجحت الاختبارات ✓'
                                : 'فشل التحقق — راجع الرسائل');
                          } catch (e) {
                            _showMsg('خطأ: $e');
                          } finally {
                            if (mounted) setState(() => _busy = false);
                          }
                        },
                  child: const Text('تشغيل الاختبارات'),
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  onPressed:
                      _busy ? null : () => _prepareAndCopyForExecutor(s),
                  icon: const Icon(Icons.copy, size: 18),
                  label: const Text('نسخ للمنفّذ'),
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  String _stageLabel(String stage) {
    switch (stage) {
      case 'idle':
        return 'جاهز للبدء';
      case 'planning_chatgpt':
        return 'ChatGPT يكتب الخطة...';
      case 'validating_claude':
        return 'Claude يراجع الخطة...';
      case 'revising_chatgpt':
        return 'ChatGPT يعدّل الخطة...';
      case 'consensus_reached':
      case 'exported':
        return 'تم الاتفاق الكامل ✓';
      case 'verifying':
        return 'جاري تشغيل الاختبارات...';
      case 'verified_done':
        return 'مكتمل وتم التحقق ✓';
      case 'paused':
        return 'متوقف مؤقتاً';
      case 'failed':
        return 'حدث خطأ';
      default:
        return stage;
    }
  }
}

class _DotStatus extends StatelessWidget {
  final bool ok;
  final String label;
  const _DotStatus({required this.ok, required this.label});

  @override
  Widget build(BuildContext context) {
    final c = ok ? const Color(0xFF16A34A) : const Color(0xFFDC2626);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: c, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(label,
            style: TextStyle(
                fontSize: 12, fontWeight: FontWeight.w600, color: c)),
      ],
    );
  }
}

class _StepChip extends StatelessWidget {
  final String step;
  final bool active;
  final bool done;
  const _StepChip({
    required this.step,
    required this.active,
    required this.done,
  });

  @override
  Widget build(BuildContext context) {
    final bg = done
        ? const Color(0xFF16A34A).withValues(alpha: 0.1)
        : active
            ? const Color(0xFF2563EB).withValues(alpha: 0.12)
            : const Color(0xFFF1F5F9);
    final fg = done
        ? const Color(0xFF15803D)
        : active
            ? const Color(0xFF2563EB)
            : const Color(0xFF64748B);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration:
          BoxDecoration(color: bg, borderRadius: BorderRadius.circular(20)),
      child: Text(step,
          style: TextStyle(
              fontSize: 11, fontWeight: FontWeight.w700, color: fg)),
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
      'CHATGPT' => 'ChatGPT — المخطط',
      'CLAUDE' => 'Claude — المدقق',
      'EXECUTOR' => 'Antigravity — المنفّذ',
      _ => 'الوسيط',
    };
    final color = switch (message.sender) {
      'CHATGPT' => const Color(0xFF059669),
      'CLAUDE' => const Color(0xFFD97706),
      'USER' => const Color(0xFF2563EB),
      _ => const Color(0xFF4F46E5),
    };

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFFE2E8F0)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(senderLabel,
                    style: TextStyle(
                        fontWeight: FontWeight.bold,
                        color: color,
                        fontSize: 12)),
              ),
              const Spacer(),
              Text('جولة ${message.roundNum}',
                  style: const TextStyle(
                      fontSize: 11, color: Color(0xFF94A3B8))),
            ],
          ),
          const SizedBox(height: 10),
          SelectableText(message.content,
              style: const TextStyle(
                  fontSize: 13.5, height: 1.55, color: Color(0xFF0F172A))),
        ],
      ),
    );
  }
}

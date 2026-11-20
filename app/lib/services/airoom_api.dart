import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/airoom_models.dart';
import 'token_loader_stub.dart'
    if (dart.library.io) 'token_loader_io.dart';

class AiroomApi {
  String baseUrl;
  String apiToken;

  AiroomApi({
    this.baseUrl = 'http://127.0.0.1:8765',
    this.apiToken = '',
  });

  String get _cleanBase => baseUrl.replaceAll(RegExp(r'/+$'), '');

  Map<String, String> get _authHeaders => {
        if (apiToken.trim().isNotEmpty) 'Authorization': 'Bearer ${apiToken.trim()}',
      };

  Future<void> ensureLocalToken() async {
    if (apiToken.trim().isNotEmpty) return;
    final tok = await readLocalDesktopToken();
    if (tok != null && tok.isNotEmpty) {
      apiToken = tok;
    }
  }

  Future<bool> checkHealth() async {
    try {
      final res = await http
          .get(Uri.parse('$_cleanBase/health'))
          .timeout(const Duration(seconds: 3));
      return res.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<CdpStatusModel> getCdpStatus(String cdpUrl) async {
    final uri = Uri.parse('$_cleanBase/api/cdp/status')
        .replace(queryParameters: {'cdp_url': cdpUrl});
    final res = await http
        .get(uri, headers: _authHeaders)
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw Exception('Failed to check CDP status: ${res.statusCode}');
    }
    return CdpStatusModel.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<void> launchChromeCdp({int port = 9223}) async {
    final uri = Uri.parse('$_cleanBase/api/cdp/ensure')
        .replace(queryParameters: {'cdp_url': 'http://127.0.0.1:$port'});
    final res = await http
        .post(uri, headers: _authHeaders)
        .timeout(const Duration(seconds: 15));
    if (res.statusCode != 200) {
      throw Exception('Failed to launch Chrome: ${res.body}');
    }
  }

  Future<Map<String, dynamic>> executeHandoff(String sessionId) async {
    final res = await http
        .post(Uri.parse('$_cleanBase/api/sessions/$sessionId/execute'),
            headers: _authHeaders)
        .timeout(const Duration(seconds: 10));
    if (res.statusCode != 200) {
      throw Exception('Execute handoff failed: ${res.body}');
    }
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> runVerifyLoop(
      String sessionId, String verifyCommand) async {
    final res = await http
        .post(
          Uri.parse('$_cleanBase/api/sessions/$sessionId/verify-loop'),
          headers: {'Content-Type': 'application/json', ..._authHeaders},
          body: jsonEncode({'verify_command': verifyCommand}),
        )
        .timeout(const Duration(seconds: 120));
    if (res.statusCode != 200) {
      throw Exception('Verify loop failed: ${res.body}');
    }
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<List<SessionModel>> listSessions() async {
    final res = await http
        .get(Uri.parse('$_cleanBase/api/sessions'), headers: _authHeaders)
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw Exception('Failed to list sessions (${res.statusCode})');
    }
    final list = jsonDecode(res.body) as List<dynamic>;
    return list
        .map((e) => SessionModel.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<SessionModel> createSession({
    required String title,
    required String userRequest,
    required String targetWorkspace,
    required String cdpUrl,
    required String mode,
    required int maxRounds,
  }) async {
    final res = await http
        .post(
          Uri.parse('$_cleanBase/api/sessions'),
          headers: {'Content-Type': 'application/json', ..._authHeaders},
          body: jsonEncode({
            'title': title,
            'user_request': userRequest,
            'target_workspace': targetWorkspace,
            'cdp_url': cdpUrl,
            'mode': mode,
            'max_rounds': maxRounds,
          }),
        )
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw Exception('Create session failed: ${res.body}');
    }
    return SessionModel.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<SessionModel> startSession(String sessionId) async {
    final res = await http
        .post(Uri.parse('$_cleanBase/api/sessions/$sessionId/start'),
            headers: _authHeaders)
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw Exception('Start failed: ${res.body}');
    }
    return SessionModel.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<SessionModel> pauseSession(String sessionId) async {
    final res = await http
        .post(Uri.parse('$_cleanBase/api/sessions/$sessionId/pause'),
            headers: _authHeaders)
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw Exception('Pause failed: ${res.body}');
    }
    return SessionModel.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<SessionModel> exportSession(String sessionId) async {
    final res = await http
        .post(Uri.parse('$_cleanBase/api/sessions/$sessionId/export'),
            headers: _authHeaders)
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw Exception('Export failed: ${res.body}');
    }
    return SessionModel.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<String> readArtifact(String sessionId, String filename) async {
    final uri = Uri.parse('$_cleanBase/api/sessions/$sessionId/artifacts/read')
        .replace(queryParameters: {'filename': filename});
    final res = await http
        .get(uri, headers: _authHeaders)
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw Exception('Read artifact failed: ${res.body}');
    }
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    return data['content']?.toString() ?? '';
  }

  Future<Uint8List> fetchBrowserScreenshot(String role,
      {String cdpUrl = 'http://127.0.0.1:9223'}) async {
    final uri = Uri.parse('$_cleanBase/api/cdp/screenshot')
        .replace(queryParameters: {'role': role, 'cdp_url': cdpUrl});
    final res = await http
        .get(uri, headers: _authHeaders)
        .timeout(const Duration(seconds: 10));
    if (res.statusCode != 200) {
      throw Exception('Screenshot failed: ${res.statusCode}');
    }
    return res.bodyBytes;
  }

  Future<void> interactBrowser({
    required String role,
    required String action,
    String cdpUrl = 'http://127.0.0.1:9223',
    double? x,
    double? y,
    String? text,
    String? key,
    double deltaY = 0,
    String? url,
  }) async {
    final res = await http
        .post(
          Uri.parse('$_cleanBase/api/cdp/interact'),
          headers: {'Content-Type': 'application/json', ..._authHeaders},
          body: jsonEncode({
            'cdp_url': cdpUrl,
            'role': role,
            'action': action,
            'x': x,
            'y': y,
            'text': text,
            'key': key,
            'delta_y': deltaY,
            'url': url,
          }),
        )
        .timeout(const Duration(seconds: 10));
    if (res.statusCode != 200) {
      throw Exception('Interact failed: ${res.body}');
    }
  }

  WebSocketChannel connectSessionWs(String sessionId, {int? lastSeq}) {
    final wsBase = _cleanBase
        .replaceFirst('https://', 'wss://')
        .replaceFirst('http://', 'ws://');
    final params = <String, String>{};
    if (lastSeq != null) params['last_seq'] = lastSeq.toString();
    if (apiToken.trim().isNotEmpty) params['token'] = apiToken.trim();
    final uri = Uri.parse('$wsBase/ws/sessions/$sessionId')
        .replace(queryParameters: params.isEmpty ? null : params);
    return WebSocketChannel.connect(uri);
  }
}

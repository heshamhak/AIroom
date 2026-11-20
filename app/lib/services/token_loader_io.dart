import 'dart:io';

Future<String?> readLocalDesktopToken() async {
  final envTokenPath = Platform.environment['AIROOM_TOKEN_PATH'];
  final xdg = Platform.environment['XDG_DATA_HOME'];
  final home = Platform.environment['HOME'];

  final candidates = <String>[
    if (envTokenPath != null && envTokenPath.isNotEmpty) envTokenPath,
    'backend/data/api_token',
    '../backend/data/api_token',
    if (xdg != null && xdg.isNotEmpty) '$xdg/airoom/api_token',
    if (home != null && home.isNotEmpty) '$home/.local/share/airoom/api_token',
  ];

  for (final path in candidates) {
    final file = File(path);
    if (await file.exists()) {
      final content = (await file.readAsString()).trim();
      if (content.isNotEmpty) return content;
    }
  }
  return null;
}

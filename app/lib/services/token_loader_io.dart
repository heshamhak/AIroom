import 'dart:io';

Future<String?> readLocalDesktopToken() async {
  final sep = Platform.pathSeparator;
  final envTokenPath = Platform.environment['AIROOM_TOKEN_PATH'];
  final xdg = Platform.environment['XDG_DATA_HOME'];
  final localApp = Platform.environment['LOCALAPPDATA'];
  final home = Platform.environment['HOME'] ?? Platform.environment['USERPROFILE'];

  final candidates = <String>[
    if (envTokenPath != null && envTokenPath.isNotEmpty) envTokenPath,
    'backend${sep}data${sep}api_token',
    '..${sep}backend${sep}data${sep}api_token',
    if (localApp != null && localApp.isNotEmpty) '$localApp${sep}airoom${sep}api_token',
    if (xdg != null && xdg.isNotEmpty) '$xdg${sep}airoom${sep}api_token',
    if (home != null && home.isNotEmpty) '$home$sep.local${sep}share${sep}airoom${sep}api_token',
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

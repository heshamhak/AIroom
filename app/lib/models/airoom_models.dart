class PlanItemModel {
  final String id;
  final String title;
  final String details;
  final String status;
  final String? revisionNote;

  PlanItemModel({
    required this.id,
    required this.title,
    required this.details,
    required this.status,
    this.revisionNote,
  });

  factory PlanItemModel.fromJson(Map<String, dynamic> json) {
    return PlanItemModel(
      id: json['id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      details: json['details']?.toString() ?? '',
      status: json['status']?.toString() ?? 'PENDING',
      revisionNote: json['revision_note']?.toString(),
    );
  }
}

class RelayMessageModel {
  final String id;
  final int seq;
  final int roundNum;
  final String sender;
  final String recipient;
  final String content;
  final String timestamp;

  RelayMessageModel({
    required this.id,
    this.seq = 0,
    required this.roundNum,
    required this.sender,
    required this.recipient,
    required this.content,
    required this.timestamp,
  });

  factory RelayMessageModel.fromJson(Map<String, dynamic> json) {
    return RelayMessageModel(
      id: json['id']?.toString() ?? '',
      seq: (json['seq'] as num?)?.toInt() ?? 0,
      roundNum: (json['round_num'] as num?)?.toInt() ?? 0,
      sender: json['sender']?.toString() ?? '',
      recipient: json['recipient']?.toString() ?? '',
      content: json['content']?.toString() ?? '',
      timestamp: json['timestamp']?.toString() ?? '',
    );
  }
}

class SessionModel {
  final String id;
  final String title;
  final String userRequest;
  final String targetWorkspace;
  final String cdpUrl;
  final String mode;
  final int maxRounds;
  final int currentRound;
  final String stage;
  final List<PlanItemModel> planItems;
  final List<RelayMessageModel> messages;
  final String finalPlanMarkdown;
  final String executorPrompt;
  final List<String> exportedFiles;
  final String? error;

  SessionModel({
    required this.id,
    required this.title,
    required this.userRequest,
    required this.targetWorkspace,
    required this.cdpUrl,
    required this.mode,
    required this.maxRounds,
    required this.currentRound,
    required this.stage,
    required this.planItems,
    required this.messages,
    required this.finalPlanMarkdown,
    required this.executorPrompt,
    required this.exportedFiles,
    this.error,
  });

  factory SessionModel.fromJson(Map<String, dynamic> json) {
    return SessionModel(
      id: json['id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      userRequest: json['user_request']?.toString() ?? '',
      targetWorkspace: json['target_workspace']?.toString() ?? '',
      cdpUrl: json['cdp_url']?.toString() ?? 'http://127.0.0.1:9222',
      mode: json['mode']?.toString() ?? 'live_cdp',
      maxRounds: (json['max_rounds'] as num?)?.toInt() ?? 4,
      currentRound: (json['current_round'] as num?)?.toInt() ?? 0,
      stage: json['stage']?.toString() ?? 'idle',
      planItems: (json['plan_items'] as List<dynamic>? ?? [])
          .map((e) => PlanItemModel.fromJson(e as Map<String, dynamic>))
          .toList(),
      messages: (json['messages'] as List<dynamic>? ?? [])
          .map((e) => RelayMessageModel.fromJson(e as Map<String, dynamic>))
          .toList(),
      finalPlanMarkdown: json['final_plan_markdown']?.toString() ?? '',
      executorPrompt: json['executor_prompt']?.toString() ?? '',
      exportedFiles: (json['exported_files'] as List<dynamic>? ?? [])
          .map((e) => e.toString())
          .toList(),
      error: json['error']?.toString(),
    );
  }
}

class CdpStatusModel {
  final bool connected;
  final String cdpUrl;
  final String? browser;
  final bool chatgptReady;
  final bool claudeReady;
  final String? error;

  CdpStatusModel({
    required this.connected,
    required this.cdpUrl,
    this.browser,
    required this.chatgptReady,
    required this.claudeReady,
    this.error,
  });

  factory CdpStatusModel.fromJson(Map<String, dynamic> json) {
    return CdpStatusModel(
      connected: json['connected'] == true,
      cdpUrl: json['cdp_url']?.toString() ?? '',
      browser: json['browser']?.toString(),
      chatgptReady: json['chatgpt_ready'] == true,
      claudeReady: json['claude_ready'] == true,
      error: json['error']?.toString(),
    );
  }
}

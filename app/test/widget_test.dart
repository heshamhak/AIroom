import 'package:flutter_test/flutter_test.dart';
import 'package:airoom/main.dart';

void main() {
  testWidgets('AiroomApp renders clean uncluttered UI', (WidgetTester tester) async {
    await tester.pumpWidget(const AiroomApp());
    expect(find.text('AIroom'), findsOneWidget);
    expect(find.text('ابدأ التخطيط'), findsOneWidget);
  });
}

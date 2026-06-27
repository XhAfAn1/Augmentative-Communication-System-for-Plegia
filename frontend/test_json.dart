import 'dart:convert';
import 'package:http/http.dart' as http;

void main() async {
  try {
    final response = await http.post(
      Uri.parse('http://127.0.0.1:8000/api/type'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'text': 'th'}),
    );
    print('Response status: ${response.statusCode}');
    print('Response body: ${response.body}');
    
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      final suggestions = data['suggestions'] as List;
      final predictions = suggestions.map((e) => e['word'] as String).toList();
      print('Predictions: $predictions');
    }
  } catch (e) {
    print('Error: $e');
  }
}

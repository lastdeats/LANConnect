"""Static contract check for automatic receiving code; not a GUI integration test."""
from pathlib import Path
source=(Path(__file__).parent/'client.py').read_text(encoding='utf-8')
for required in ('def auto_receive(self):','f[\'target\']==me','partial.open(\'ab\')','partial.replace(path)','self.auto_download=tk.BooleanVar(value=True)','self.render_chat();self.auto_receive()'):
 assert required in source,required
print('PASS: auto-receive wiring and resume path present (static only)')

"""Compile and execute shared pure Java code; no Android SDK or hardware mock."""
from pathlib import Path
import shutil, subprocess, tempfile, time
root=Path(__file__).resolve().parent
java=shutil.which('java');javac=shutil.which('javac')
if not java:raise SystemExit('Java runtime with jdk.compiler required')
compiler=[javac] if javac else [java,'-m','jdk.compiler/com.sun.tools.javac.Main']
with tempfile.TemporaryDirectory() as td:
 sources=[root/'src/org/tambov/lanconnect/PcmResampler.java',root/'src/org/tambov/lanconnect/BoundedStream.java',root/'src/org/tambov/lanconnect/AudioSelector.java',root/'src/org/tambov/lanconnect/ImageSizing.java',root/'src/org/tambov/lanconnect/ConsentGuard.java',root/'tests/CompatibilityTest.java']
 print('Android compatibility: compiling Java sources', flush=True)
 subprocess.run([*compiler,'-source','8','-target','8','-Xlint:-options','-encoding','UTF-8','-d',td,*map(str,sources)],check=True,timeout=45)
 for path in Path(td).rglob('*.class'):
  if int.from_bytes(path.read_bytes()[6:8],'big')!=52:raise AssertionError('Expected Java 8 bytecode')
 print('Android compatibility: running Java tests', flush=True)
 subprocess.run([java,'-cp',td,'CompatibilityTest'],check=True,timeout=30)
 print('Android compatibility: passed', flush=True)

import javax.tools.*;
import com.sun.source.util.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Stream;

/** Host JDK syntax check only. Does NOT resolve Android API symbols or build an APK. */
public class CheckJavaSyntax {
    public static void main(String[] args) throws Exception {
        JavaCompiler compiler=ToolProvider.getSystemJavaCompiler();
        if(compiler==null)throw new IllegalStateException("JDK compiler module is required");
        ArrayList<Path> sources=new ArrayList<>();
        for(String arg:args){
            Path path=Paths.get(arg);
            if(Files.isDirectory(path)){
                try(Stream<Path> files=Files.walk(path)){files.filter(p->p.toString().endsWith(".java")).sorted().forEach(sources::add);}
            }else if(Files.isRegularFile(path))sources.add(path);
            else throw new IllegalArgumentException("Source not found: "+arg);
        }
        if(sources.isEmpty())throw new IllegalArgumentException("No Java sources supplied");
        DiagnosticCollector<JavaFileObject> diagnostics=new DiagnosticCollector<>();
        try(StandardJavaFileManager fm=compiler.getStandardFileManager(diagnostics,null,StandardCharsets.UTF_8)){
            JavacTask task=(JavacTask)compiler.getTask(null,fm,diagnostics,Arrays.asList("-proc:none"),null,fm.getJavaFileObjectsFromPaths(sources));
            task.parse();int errors=0;
            for(Diagnostic<?> d:diagnostics.getDiagnostics())if(d.getKind()==Diagnostic.Kind.ERROR){System.out.println(d);errors++;}
            if(errors>0)System.exit(1);
            System.out.println("PASS: "+sources.size()+" Java files parsed (no Android SDK type resolution or APK compilation)");
        }
    }
}

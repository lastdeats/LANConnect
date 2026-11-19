package org.tambov.lanconnect;

import java.io.*;

/** Bounded streaming helpers shared with actual provider upload logic. */
public final class BoundedStream {
    private BoundedStream() {}
    public static int readChunk(InputStream stream,byte[] buffer) throws IOException {
        if(buffer.length==0)throw new IllegalArgumentException("Empty buffer");
        int used=0;
        while(used<buffer.length){
            if(Thread.currentThread().isInterrupted())throw new InterruptedIOException("Операция отменена");
            int n=stream.read(buffer,used,buffer.length-used);
            if(n==-1)break;
            if(n==0)throw new IOException("Источник файла не отвечает");
            used+=n;
        }
        return used;
    }
    public static long measure(InputStream stream,long limit) throws IOException {
        if(limit<0)throw new IllegalArgumentException("Negative limit");
        byte[] buffer=new byte[64*1024];long total=0;int n;
        while((n=readChunk(stream,buffer))>0){
            if(n>limit-total)throw new IOException("Максимальный размер файла — 5 ГиБ");
            total+=n;
        }
        return total;
    }
}

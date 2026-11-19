package org.tambov.lanconnect;

/** Bound decoded screen-frame dimensions on phones with smaller Java heaps. */
public final class ImageSizing {
    public static int sample(int width,int height) {
        if(width<=0 || height<=0 || width>4096 || height>4096)throw new IllegalArgumentException("Неверный размер кадра");
        int sample=1;
        while((width+sample-1)/sample>1280 || (height+sample-1)/sample>1280)sample*=2;
        return sample;
    }
    private ImageSizing(){}
}

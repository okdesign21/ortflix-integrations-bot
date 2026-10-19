#!/bin/sh
set -eu

FFMPEG_VERSION="${1:?Usage: build_ffmpeg.sh VERSION}"

curl --fail --show-error --location --proto '=https' \
    -o ffmpeg.tar.xz "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"
curl --fail --show-error --location --proto '=https' \
    -o ffmpeg.tar.xz.asc "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz.asc"
curl --fail --show-error --location --proto '=https' \
    -o release-key.asc https://ffmpeg.org/ffmpeg-devel.asc

mkdir -m 700 /tmp/ffmpeg-gnupg
export GNUPGHOME=/tmp/ffmpeg-gnupg
gpg --batch --import release-key.asc
gpg --batch --export FCF986EA15E6E293A5644F10B4322F04D67658D8 > release-key.gpg
gpg --batch --verify --no-default-keyring --keyring ./release-key.gpg \
    ffmpeg.tar.xz.asc ffmpeg.tar.xz

tar -xJf ffmpeg.tar.xz --strip-components=1
./configure --prefix=/media --disable-autodetect --disable-shared --enable-static \
    --disable-doc --disable-debug --disable-ffplay --disable-network \
    --disable-indevs --enable-indev=lavfi --disable-outdevs \
    --enable-zlib --disable-encoders --enable-encoder=pcm_s16le,alac
make -j2
make install-progs

mkdir -p /media/usr/local/bin /media/usr/local/share/ffmpeg /media/DEBIAN
mv /media/bin/ffmpeg /media/bin/ffprobe /media/usr/local/bin/
rmdir /media/bin
cp COPYING.LGPLv2.1 LICENSE.md ffbuild/config.log \
    ffmpeg.tar.xz ffmpeg.tar.xz.asc release-key.asc /media/usr/local/share/ffmpeg/
printf '%s\n' "FFmpeg ${FFMPEG_VERSION}" \
    "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    > /media/usr/local/share/ffmpeg/source.txt
printf '%s\n' 'Package: ffmpeg' 'Source: ffmpeg' \
    "Version: 7:${FFMPEG_VERSION}-0ortflix1" \
    "Architecture: $(dpkg --print-architecture)" \
    'Maintainer: Ortflix <ortflix@localhost>' \
    'Depends: libc6, zlib1g' \
    'Description: Ortflix upstream FFmpeg build for local probe and remux' \
    > /media/DEBIAN/control
dpkg-deb --root-owner-group --build /media /ffmpeg.deb
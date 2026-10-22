#!/bin/bash
# One-time, attended commissioning task. Recovery never reruns this installer.
set -euo pipefail
export PATH=/usr/syno/bin:/usr/syno/sbin:/usr/bin:/usr/sbin:/bin:/sbin
[[ $(id -u) == 0 ]] || { echo 'Root is required.' >&2; exit 1; }
[[ $(cat /proc/sys/kernel/syno_hw_version) == RS819 ]] || { echo 'Unexpected NAS model.' >&2; exit 1; }
get=$(command -v synogetkeyvalue)
put=$(command -v synosetkeyvalue)
[[ $($get /etc.defaults/VERSION productversion) == 7.4.1 && $($get /etc.defaults/VERSION buildnumber) == 90080 && $($get /etc.defaults/VERSION smallfixnumber) == 1 ]] || {
    echo 'This DSM version has not been reviewed for initial installation.' >&2; exit 1;
}
[[ ! -e /var/packages/ContainerManager/INFO ]] || { echo 'Container Manager already exists; no upgrade attempted.' >&2; exit 1; }
[[ -d /volume1 ]] || { echo 'Expected installation volume is absent.' >&2; exit 1; }
mkdir /var/run/alphaess-container-install.lock || { echo 'Installation is already running.' >&2; exit 1; }
work=/volume1/@alphaess-ops
mkdir -p "$work"
chmod 700 "$work"
umask 077
original=$($get /etc/synoinfo.conf unique)
original_defaults=$($get /etc.defaults/synoinfo.conf unique)
restore() {
    "$put" /etc/synoinfo.conf unique "$original"
    "$put" /etc.defaults/synoinfo.conf unique "$original_defaults"
    rmdir /var/run/alphaess-container-install.lock
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ $original == synology_rtd1296_rs819 && $original_defaults == synology_rtd1296_rs819 ]] || {
    echo 'Unexpected model configuration; review before proceeding.' >&2; exit 1;
}
stamp=$(date +%Y%m%d-%H%M%S)
cp -p /etc/synoinfo.conf "$work/synoinfo-$stamp.conf"
cp -p /etc.defaults/synoinfo.conf "$work/defaults-synoinfo-$stamp.conf"
chmod 600 "$work/"*"$stamp.conf"
exec > >(tee -a "$work/install-$stamp.log") 2>&1
url=https://raw.githubusercontent.com/007revad/ContainerManager_for_all_armv8/2f0ffa47a84316be7fc6423c28b6a763797a24e2/install_container_manager.sh
curl --fail --silent --show-error --location --max-time 60 "$url" -o "$work/installer-original.sh"
printf '%s  %s\n' de015c4c168d9415ebcdcfd492a1449eeff71f9237a95ff76330772f648fb5ef "$work/installer-original.sh" | sha256sum -c -
# Both upstream prompts become explicit failures, rather than unattended loops.
[[ $(grep -c '^[[:space:]]*until read -r -t20 ' "$work/installer-original.sh") == 2 ]]
awk '/^[[:space:]]*until read -r -t20 / {
    print "    echo Unexpected_interactive_step_requires_administrator_review >&2"
    print "    restore_unique"
    print "    exit 20"
    next
} {print}' "$work/installer-original.sh" > "$work/installer-bounded.sh"
# The initial-install path uses these values to avoid an interactive volume choice.
timeout 300 env target=/volume1 targetvol=/volume1 bash "$work/installer-bounded.sh" </dev/null
[[ -f /var/packages/ContainerManager/INFO ]]
"$put" /etc/synoinfo.conf unique "$original"
"$put" /etc.defaults/synoinfo.conf unique "$original_defaults"
docker=/var/packages/ContainerManager/target/usr/bin/docker
for attempt in $(seq 1 18); do
    if "$docker" --host unix:///var/run/docker.sock info >/dev/null 2>&1; then
        echo 'PASS: Container Manager installed, model identifiers restored, Docker responding.'
        exit 0
    fi
    sleep 5
done
echo 'Package installed but Docker did not become ready; inspect the installation log.' >&2
exit 1

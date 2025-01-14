#!/bin/bash

# SPDX-FileCopyrightText: 2022 Synacor, Inc.
# SPDX-FileCopyrightText: 2022 Zextras <https://www.zextras.com>
#
# SPDX-License-Identifier: GPL-2.0-only

autoTrainSystem() {

  timestampit "Starting spam/ham extraction from system accounts."
  spamdir=$(mktmpdir spam)
  hamdir=$(mktmpdir ham)
  /opt/zextras/libexec/zmspamextract "${spam_account}" -o "${spamdir}"
  /opt/zextras/libexec/zmspamextract "${ham_account}" -o "${hamdir}"
  timestampit "Finished extracting spam/ham from system accounts."

  timestampit "Starting spamassassin training."
  /opt/zextras/common/bin/sa-learn \
    --dbpath=${db_path} -L --no-sync \
    --spam "${spamdir}"

  /opt/zextras/common/bin/sa-learn \
    --dbpath=${db_path} -L --no-sync \
    --ham "${hamdir}"

  /opt/zextras/common/bin/sa-learn \
    --dbpath=${db_path} --sync
  timestampit "Finished spamassassin training."

  if [ "$amavis_dspam_enabled" = "true" ]; then
    timestampit "Starting dspam training"
    /opt/zextras/dspam/bin/dspam_train zimbra "${spamdir}" "${hamdir}"
    /opt/zextras/dspam/bin/dspam_clean -p0 "$USER"
    timestampit "Finished dspam training"
  fi

  rm -rf "${spamdir}" "${hamdir}"
}

trainAccountFolder() {

  tempdir=$(mktmpdir "${MODE}")
  if [ "${MODE}" = "spam" ]; then
    FOLDER=${FOLDER:=junk}
  elif [ "${MODE}" = "ham" ]; then
    FOLDER=${FOLDER:=inbox}
  fi

  timestampit "Starting spamassassin $MODE training for $USER using folder $FOLDER"
  /opt/zextras/libexec/zmspamextract -r -m "$USER" -o "${tempdir}" -q in:"${FOLDER}"

  /opt/zextras/common/bin/sa-learn \
    --dbpath=${db_path} -L --no-sync \
    --"${MODE}" "${tempdir}"

  /opt/zextras/common/bin/sa-learn \
    --dbpath=${db_path} --sync
  timestampit "Finished spamassassin $MODE training for $USER using folder $FOLDER"

  if [ "$amavis_dspam_enabled" = "true" ]; then
    timestampit "Starting dspam $MODE training for $USER using folder $FOLDER"
    if [ "$MODE" == "ham" ]; then
      MODE="innocent"
    fi

    /opt/zextras/dspam/bin/dspam --user zextras --class="${MODE}" --source=corpus --mode=teft \
      --feature=noise --stdout

    /opt/zextras/dspam/bin/dspam_clean -p0 "$USER"
    timestampit "Finished dspam $MODE training for $USER using folder $FOLDER"
  fi

  rm -rf "${tempdir}"

}

mktmpdir() {
  mktemp -d "${zmtrainsa_tmp_directory:-${zimbra_tmp_directory}}/zmtrainsa.$$.$1.XXXXXX" || exit 1
}

timestampit() {
  SIMPLE_DATE=$(date +%Y%m%d%H%M%S)
  echo "$SIMPLE_DATE $1"
}

usage() {
  echo "Usage: $0 <user> <spam|ham> [folder]"
  exit 1
}

if [ "$(whoami)" != zextras ]; then
  echo Error: must be run as zextras user
  exit 1
fi

if [ ! -x "/opt/zextras/common/sbin/amavisd" ]; then
  echo "Error: SpamAssassin not installed"
  exit 1
fi

source /opt/zextras/bin/zmshutil || exit 1
zmsetvars

amavis_dspam_enabled=$(/opt/zextras/bin/zmprov -l gs "${zimbra_server_hostname}" zimbraAmavisDSPAMEnabled | grep zimbraAmavisDSPAMEnabled: | awk '{print $2}')
amavis_dspam_enabled=$(echo "$amavis_dspam_enabled" | tr "[:upper:]" "[:lower:]")
antispam_mysql_enabled=$(echo "$antispam_mysql_enabled" | tr "[:upper:]" "[:lower:]")
zmtrainsa_cleanup_host=$(echo "$zmtrainsa_cleanup_host" | tr "[:upper:]" "[:lower:]")

if [ "${zimbra_spam_externalIsSpamAccount}" = "" ]; then
  spam_account="-s"
else
  spam_account="-m ${zimbra_spam_externalIsSpamAccount}"
fi

if [ "${zimbra_spam_externalIsNotSpamAccount}" = "" ]; then
  ham_account="-n"
else
  ham_account="-m ${zimbra_spam_externalIsNotSpamAccount}"
fi

# Set db_path
if [ "$antispam_mysql_enabled" = "true" ]; then
  db_path='/opt/zextras/data/amavisd/mysql/data'
else
  db_path='/opt/zextras/data/amavisd/.spamassassin'
fi

# No argument mode uses zmspamextract for auto-training.
if [ "$1" = "" ]; then
  autoTrainSystem
  exit
fi

if [ "$1" = "--cleanup" ]; then
  if [ "${zmtrainsa_cleanup_host}" = "true" ]; then
    timestampit "Starting spam/ham cleanup"
    mydir=$(mktmpdir cleanup)
    /opt/zextras/libexec/zmspamextract "${spam_account}" -o "${mydir}" -d
    /opt/zextras/libexec/zmspamextract "${ham_account}" -o "${mydir}" -d
    rm -rf "${mydir}"
    timestampit "Finished spam/ham cleanup"
  else
    timestampit "Cleanup skipped: $zimbra_server_hostname is not a spam/ham cleanup host."
  fi
  exit
fi

USER=$1
MODE=$(echo "$2" | tr "[:upper:]" "[:lower:]")
FOLDER=$3

if [ "${MODE}" != "spam" ] && [ "${MODE}" != "ham" ]; then
  usage
fi

if [ "${USER}" = "" ]; then
  usage
fi

trainAccountFolder

exit 0

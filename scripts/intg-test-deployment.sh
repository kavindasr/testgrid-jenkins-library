#!/bin/bash
# -------------------------------------------------------------------------------------
# Copyright (c) 2022 WSO2 Inc. (http://www.wso2.org) All Rights Reserved.
#
# WSO2 Inc. licenses this file to you under the Apache License,
# Version 2.0 (the "License"); you may not use this file except
# in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# --------------------------------------------------------------------------------------

deploymentName=$1
productRepository=$2
productTestBranch=$3
productTestScript=$4
productTestGroup=$5
currentScript=$(dirname $(realpath "$0"))

deploymentDirectory="${WORKSPACE}/deployment/${deploymentName}"
parameterFilePath="${deploymentDirectory}/parameters.json"
# Each test group runs in its own parallel branch against its own EC2, so keep every
# group's clone and output directories separate to avoid clobbering one another.
groupTag="${productTestGroup:-default}"
testOutputDir="${deploymentDirectory}/outputs/${groupTag}"
cloneBaseDir="${deploymentDirectory}/${groupTag}"
productDirectory="product-apim"

source ${currentScript}/common-functions.sh

productDirectoryLocation=""

function cloneTestRepo(){
    local githubUsername=$(extractParameters "GithubUserName" ${parameterFilePath})
    local githubPassword=$(extractParameters "GithubPassword" ${parameterFilePath})
    local cloneString=$(echo ${productRepository} | sed  's#https://#&'${githubUsername}':'${githubPassword}@'#')
    local repoName="$(basename ${productRepository} .git)"

    log_info "Cloning product repo to get test scripts"
    log_info "Product repo ${productRepository}"

    # Clone into a per-group directory so parallel group branches do not race on a shared
    # clone. Teardown is now handled once by the pipeline after all group branches finish.
    mkdir -p "${cloneBaseDir}"
    if [ ! -d  "${cloneBaseDir}/${repoName}" ];
     then
      git -C ${cloneBaseDir} clone ${cloneString} --branch ${productTestBranch}
      if [[ $? != 0 ]];
        then
          log_error "Testing repo clone failed! Please check if the Git credentials or the test repo name is correct."
          exit 1
        else
          log_info "Cloning the test repo was successfull!"
      fi
    fi

    log_info "Product repo name ${repoName}"

    productDirectoryLocation="${cloneBaseDir}/${repoName}"
}

function deploymentTest(){
    log_info "Creating output directory"
    # testOutputDir is per-group, so clearing it only affects this group's branch.
    if [ -d "${testOutputDir}" ]; then
        log_error "Output directory already exists. Removing the existing output directory."
        rm -r "${testOutputDir}"
    fi
    mkdir -p ${testOutputDir}
    log_info "Executing scenario tests for ${productTestGroup}!"
    bash ${currentScript}/intg-test-executer.sh "${deploymentDirectory}" "${testOutputDir}" "${productTestGroup}"
    # Do NOT tear the stack down here: sibling group branches share this stack and are
    # still running. Fail the branch on error; the pipeline deletes the stack once, after
    # every group branch for this deployment has completed.
    if [[ $? != 0 ]];
    then
        log_error "Integration test execution failed for group ${productTestGroup}!"
        exit 1
    else
        log_info "Test Execution Passed!"
    fi
}

function main(){
    cloneTestRepo
    deploymentTest
}

main

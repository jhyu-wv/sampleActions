#!/usr/bin/env python3
"""
RSS to GitHub Project V2 동기화 스크립트
멘티스 RSS에서 이슈를 가져와 GitHub Project V2에 동기화
"""

import os
import sys
import json
import requests
import feedparser
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse


class GitHubProjectSync:
    def __init__(self):
        self.token = os.environ['GITHUB_TOKEN']
        self.repository = os.environ['REPOSITORY']
        self.rss_url = os.environ['MENTIS_RSS_URL']
        self.project_number = os.environ.get('PROJECT_NUMBER')  # 프로젝트 번호
        self.project_id = os.environ.get('PROJECT_ID')  # GraphQL Node ID
        self.default_status = os.environ['DEFAULT_STATUS']
        self.default_milestone = os.environ['DEFAULT_MILESTONE']
        
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'
        }
        self.graphql_url = 'https://api.github.com/graphql'
        self.rest_api_url = 'https://api.github.com/repos'
        
        # PROJECT_ID가 없으면 PROJECT_NUMBER로부터 가져오기
        if not self.project_id and self.project_number:
            self.project_id = self.get_project_id_from_number()
    
    def get_project_id_from_number(self) -> Optional[str]:
        """프로젝트 번호로부터 GraphQL Node ID 가져오기"""
        try:
            # 사용자/조직의 프로젝트인지 확인
            owner = self.repository.split('/')[0]
            
            # 먼저 사용자 프로젝트로 시도
            query = """
            query($login: String!, $number: Int!) {
                user(login: $login) {
                    projectV2(number: $number) {
                        id
                    }
                }
            }
            """
            
            variables = {
                'login': owner,
                'number': int(self.project_number)
            }
            
            response = requests.post(
                self.graphql_url,
                headers=self.headers,
                json={'query': query, 'variables': variables}
            )
            response.raise_for_status()
            
            result = response.json()
            if result.get('data', {}).get('user', {}).get('projectV2'):
                project_id = result['data']['user']['projectV2']['id']
                print(f"사용자 프로젝트 ID 조회: {project_id}")
                return project_id
            
            # 조직 프로젝트로 시도
            query = """
            query($login: String!, $number: Int!) {
                organization(login: $login) {
                    projectV2(number: $number) {
                        id
                    }
                }
            }
            """
            
            response = requests.post(
                self.graphql_url,
                headers=self.headers,
                json={'query': query, 'variables': variables}
            )
            response.raise_for_status()
            
            result = response.json()
            if result.get('data', {}).get('organization', {}).get('projectV2'):
                project_id = result['data']['organization']['projectV2']['id']
                print(f"조직 프로젝트 ID 조회: {project_id}")
                return project_id
                
            print("프로젝트를 찾을 수 없습니다.")
            return None
            
        except Exception as e:
            print(f"프로젝트 ID 조회 실패: {e}")
            return None
        
    def fetch_rss_items(self) -> List[Dict]:
        """RSS 피드에서 아이템 가져오기"""
        try:
            feed = feedparser.parse(self.rss_url)
            items = []
            
            for entry in feed.entries:
                items.append({
                    'title': entry.title,
                    'url': entry.link,
                    'published': entry.get('published', ''),
                    'description': entry.get('summary', entry.get('description', ''))
                })
                
            print(f"RSS에서 {len(items)}개 아이템 조회")
            return items
            
        except Exception as e:
            print(f"RSS 피드 조회 실패: {e}")
            return []
    
    def get_existing_issues(self) -> Dict[str, Dict]:
        """기존 이슈 목록 조회 (URL을 키로 사용)"""
        try:
            url = f"{self.rest_api_url}/{self.repository}/issues"
            params = {'state': 'all', 'per_page': 100}
            
            existing_issues = {}
            page = 1
            
            while True:
                params['page'] = page
                response = requests.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                
                issues = response.json()
                if not issues:
                    break
                    
                for issue in issues:
                    # 이슈 본문에서 원본 URL 추출
                    body = issue.get('body', '')
                    if 'Original URL:' in body:
                        original_url = body.split('Original URL:')[1].strip().split('\n')[0].strip()
                        existing_issues[original_url] = {
                            'number': issue['number'],
                            'title': issue['title']
                        }
                
                page += 1
                
            print(f"기존 이슈 {len(existing_issues)}개 조회")
            return existing_issues
            
        except Exception as e:
            print(f"기존 이슈 조회 실패: {e}")
            return {}
    
    def create_issue(self, item: Dict) -> Optional[int]:
        """새 이슈 생성"""
        try:
            url = f"{self.rest_api_url}/{self.repository}/issues"
            
            body = f"{item['description']}\n\nOriginal URL: {item['url']}"
            
            data = {
                'title': item['title'],
                'body': body,
                'labels': ['mantis-rss', 'QA']
            }
            
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            
            issue = response.json()
            print(f"이슈 생성: #{issue['number']} - {issue['title']}")
            return issue['number']
            
        except Exception as e:
            print(f"이슈 생성 실패: {e}")
            return None
    
    def get_project_fields(self) -> Dict:
        """프로젝트 필드 정보 조회"""
        query = """
        query($projectId: ID!) {
            node(id: $projectId) {
                ... on ProjectV2 {
                    fields(first: 20) {
                        nodes {
                            ... on ProjectV2Field {
                                id
                                name
                            }
                            ... on ProjectV2SingleSelectField {
                                id
                                name
                                options {
                                    id
                                    name
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        
        try:
            response = requests.post(
                self.graphql_url,
                headers=self.headers,
                json={'query': query, 'variables': {'projectId': self.project_id}}
            )
            response.raise_for_status()
            
            data = response.json()['data']['node']['fields']['nodes']
            
            fields = {}
            for field in data:
                field_name = field['name']
                field_id = field['id']
                
                if 'options' in field:  # SingleSelect 필드
                    options = {opt['name']: opt['id'] for opt in field['options']}
                    fields[field_name] = {'id': field_id, 'options': options}
                else:
                    fields[field_name] = {'id': field_id}
                    
            return fields
            
        except Exception as e:
            print(f"프로젝트 필드 조회 실패: {e}")
            return {}
    
    def add_issue_to_project(self, issue_number: int) -> Optional[str]:
        """이슈를 프로젝트에 추가"""
        # 먼저 이슈의 node_id를 가져옴
        try:
            url = f"{self.rest_api_url}/{self.repository}/issues/{issue_number}"
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            issue_node_id = response.json()['node_id']
            
            # 프로젝트에 이슈 추가
            mutation = """
            mutation($projectId: ID!, $contentId: ID!) {
                addProjectV2Item(input: {
                    projectId: $projectId
                    contentId: $contentId
                }) {
                    item {
                        id
                    }
                }
            }
            """
            
            variables = {
                'projectId': self.project_id,
                'contentId': issue_node_id
            }
            
            response = requests.post(
                self.graphql_url,
                headers=self.headers,
                json={'query': mutation, 'variables': variables}
            )
            response.raise_for_status()
            
            result = response.json()
            if 'errors' in result:
                print(f"프로젝트 추가 오류: {result['errors']}")
                # 다른 방법으로 시도
                return self._add_issue_to_project_alternative(issue_number, issue_node_id)
                
            item_id = result['data']['addProjectV2Item']['item']['id']
            print(f"이슈 #{issue_number}를 프로젝트에 추가")
            return item_id
            
        except Exception as e:
            print(f"프로젝트 추가 실패: {e}")
            return self._add_issue_to_project_alternative(issue_number, issue_node_id)
    
    def _add_issue_to_project_alternative(self, issue_number: int, issue_node_id: str) -> Optional[str]:
        """대안적인 방법으로 이슈를 프로젝트에 추가"""
        try:
            # REST API를 사용한 방법
            url = f"https://api.github.com/projects/{self.project_id}/items"
            
            data = {
                'content_id': issue_node_id,
                'content_type': 'Issue'
            }
            
            response = requests.post(url, headers=self.headers, json=data)
            
            if response.status_code == 201:
                result = response.json()
                print(f"대안 방법으로 이슈 #{issue_number}를 프로젝트에 추가")
                return result.get('id')
            else:
                print(f"대안 방법도 실패: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"대안 방법 실패: {e}")
            return None
    
    def update_project_item_fields(self, item_id: str, fields: Dict):
        """프로젝트 아이템의 필드 업데이트"""
        try:
            for field_name, field_info in fields.items():
                field_id = field_info['id']
                
                # Status 필드 설정
                if field_name.lower() == 'status' and 'options' in field_info:
                    option_id = field_info['options'].get(self.default_status)
                    if option_id:
                        self._update_single_select_field(item_id, field_id, option_id)
                
                # Milestone 필드 설정 (텍스트 필드라고 가정)
                elif field_name.lower() == 'milestone':
                    self._update_text_field(item_id, field_id, self.default_milestone)
                    
        except Exception as e:
            print(f"필드 업데이트 실패: {e}")
    
    def _update_single_select_field(self, item_id: str, field_id: str, option_id: str):
        """SingleSelect 필드 업데이트"""
        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
            updateProjectV2ItemFieldValue(input: {
                projectId: $projectId
                itemId: $itemId
                fieldId: $fieldId
                value: $value
            }) {
                projectV2Item {
                    id
                }
            }
        }
        """
        
        variables = {
            'projectId': self.project_id,
            'itemId': item_id,
            'fieldId': field_id,
            'value': {'singleSelectOptionId': option_id}
        }
        
        response = requests.post(
            self.graphql_url,
            headers=self.headers,
            json={'query': mutation, 'variables': variables}
        )
        response.raise_for_status()
    
    def _update_text_field(self, item_id: str, field_id: str, text_value: str):
        """텍스트 필드 업데이트"""
        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
            updateProjectV2ItemFieldValue(input: {
                projectId: $projectId
                itemId: $itemId
                fieldId: $fieldId
                value: $value
            }) {
                projectV2Item {
                    id
                }
            }
        }
        """
        
        variables = {
            'projectId': self.project_id,
            'itemId': item_id,
            'fieldId': field_id,
            'value': {'text': text_value}
        }
        
        response = requests.post(
            self.graphql_url,
            headers=self.headers,
            json={'query': mutation, 'variables': variables}
        )
        response.raise_for_status()
    
    def sync_rss_to_project(self):
        """RSS를 프로젝트에 동기화"""
        print("RSS to Project 동기화 시작")
        
        # RSS 아이템 조회
        rss_items = self.fetch_rss_items()
        if not rss_items:
            print("RSS 아이템이 없습니다.")
            return
        
        # 기존 이슈 조회
        existing_issues = self.get_existing_issues()
        
        # 프로젝트 필드 정보 조회
        project_fields = self.get_project_fields()
        
        new_issues_count = 0
        
        for item in rss_items:
            item_url = item['url']
            
            # 이미 존재하는 이슈인지 확인
            if item_url in existing_issues:
                print(f"이미 존재하는 이슈: {item['title']}")
                continue
            
            # 새 이슈 생성
            issue_number = self.create_issue(item)
            if not issue_number:
                continue
            
            # 프로젝트에 추가
            item_id = self.add_issue_to_project(issue_number)
            if not item_id:
                continue
            
            # 필드 설정
            self.update_project_item_fields(item_id, project_fields)
            
            new_issues_count += 1
        
        print(f"동기화 완료: {new_issues_count}개 신규 이슈 추가")


def main():
    try:
        sync = GitHubProjectSync()
        sync.sync_rss_to_project()
    except Exception as e:
        print(f"스크립트 실행 실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
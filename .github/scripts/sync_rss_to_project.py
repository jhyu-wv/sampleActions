#!/usr/bin/env python3
"""
RSS to GitHub Projects V2 동기화 스크립트
"""

import os
import sys
import json
import logging
import feedparser
from typing import Dict, List, Optional, Any
import requests
from datetime import datetime

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class GitHubProjectsAPI:
    """GitHub Projects V2 API 클라이언트"""
    
    def __init__(self, token: str, owner: str, repo: str):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'
        }
        self.graphql_url = 'https://api.github.com/graphql'
        self.rest_url = 'https://api.github.com/repos'
    
    def _make_graphql_request(self, query: str, variables: Dict = None) -> Dict:
        """GraphQL 요청 실행"""
        payload = {'query': query}
        if variables:
            payload['variables'] = variables
            
        response = requests.post(
            self.graphql_url,
            headers=self.headers,
            json=payload
        )
        
        if response.status_code != 200:
            logger.error(f"GraphQL 요청 실패: {response.status_code} - {response.text}")
            response.raise_for_status()
        
        data = response.json()
        if 'errors' in data:
            logger.error(f"GraphQL 오류: {data['errors']}")
            raise Exception(f"GraphQL 오류: {data['errors']}")
        
        return data
    
    def get_project_info(self, project_number: int) -> Optional[Dict]:
        """프로젝트 정보 조회"""
        query = """
        query($owner: String!, $projectNumber: Int!) {
            organization(login: $owner) {
                projectV2(number: $projectNumber) {
                    id
                    title
                    fields(first: 20) {
                        nodes {
                            ... on ProjectV2Field {
                                id
                                name
                                dataType
                            }
                            ... on ProjectV2SingleSelectField {
                                id
                                name
                                dataType
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
            variables = {'owner': self.owner, 'projectNumber': project_number}
            result = self._make_graphql_request(query, variables)
            
            # organization 또는 user 확인
            project_data = None
            if result.get('data', {}).get('organization'):
                project_data = result['data']['organization'].get('projectV2')
            else:
                # User 프로젝트 시도
                user_query = query.replace('organization', 'user')
                result = self._make_graphql_request(user_query, variables)
                if result.get('data', {}).get('user'):
                    project_data = result['data']['user'].get('projectV2')
            
            if not project_data:
                logger.error(f"프로젝트 {project_number}를 찾을 수 없습니다.")
                return None
                
            return project_data
            
        except Exception as e:
            logger.error(f"프로젝트 정보 조회 실패: {e}")
            return None
    
    def get_existing_issues(self, project_id: str) -> List[Dict]:
        """프로젝트의 기존 이슈 목록 조회"""
        query = """
        query($projectId: ID!, $cursor: String) {
            node(id: $projectId) {
                ... on ProjectV2 {
                    items(first: 100, after: $cursor) {
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                        nodes {
                            id
                            content {
                                ... on Issue {
                                    id
                                    title
                                    url
                                    number
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        
        all_issues = []
        cursor = None
        
        try:
            while True:
                variables = {'projectId': project_id}
                if cursor:
                    variables['cursor'] = cursor
                
                result = self._make_graphql_request(query, variables)
                
                node_data = result.get('data', {}).get('node')
                if not node_data or not node_data.get('items'):
                    break
                
                items = node_data['items']
                
                for item in items.get('nodes', []):
                    if item and item.get('content') and item['content'].get('title'):
                        content = item['content']
                        all_issues.append({
                            'title': content['title'],
                            'url': content.get('url', ''),
                            'number': content.get('number'),
                            'project_item_id': item['id']
                        })
                
                page_info = items.get('pageInfo', {})
                if not page_info.get('hasNextPage'):
                    break
                cursor = page_info.get('endCursor')
                
        except Exception as e:
            logger.error(f"기존 이슈 조회 실패: {e}")
            return []
        
        logger.info(f"기존 이슈 {len(all_issues)}개 조회 완료")
        return all_issues
    
    def create_issue(self, title: str, body: str = "", labels: List[str] = None) -> Optional[Dict]:
        """이슈 생성"""
        url = f"{self.rest_url}/{self.owner}/{self.repo}/issues"
        
        data = {
            'title': title,
            'body': body
        }
        
        if labels:
            data['labels'] = labels
        
        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            
            issue_data = response.json()
            logger.info(f"이슈 생성 완료: {title}")
            return issue_data
            
        except Exception as e:
            logger.error(f"이슈 생성 실패: {title} - {e}")
            return None
    
    def add_issue_to_project(self, project_id: str, issue_id: str) -> Optional[str]:
        """이슈를 프로젝트에 추가"""
        mutation = """
        mutation($projectId: ID!, $contentId: ID!) {
            addProjectV2ItemById(input: {
                projectId: $projectId
                contentId: $contentId
            }) {
                item {
                    id
                }
            }
        }
        """
        
        try:
            variables = {
                'projectId': project_id,
                'contentId': issue_id
            }
            
            result = self._make_graphql_request(mutation, variables)
            
            item_data = result.get('data', {}).get('addProjectV2ItemById', {}).get('item')
            if item_data:
                return item_data['id']
            
            return None
            
        except Exception as e:
            logger.error(f"프로젝트에 이슈 추가 실패: {e}")
            return None
    
    def update_project_item_field(self, project_id: str, item_id: str, field_id: str, value: Any) -> bool:
        """프로젝트 아이템 필드 업데이트"""
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
        
        try:
            variables = {
                'projectId': project_id,
                'itemId': item_id,
                'fieldId': field_id,
                'value': value
            }
            
            result = self._make_graphql_request(mutation, variables)
            
            return result.get('data', {}).get('updateProjectV2ItemFieldValue') is not None
            
        except Exception as e:
            logger.error(f"프로젝트 필드 업데이트 실패: {e}")
            return False

class RSSProjectSync:
    """RSS를 GitHub 프로젝트와 동기화하는 클래스"""
    
    def __init__(self):
        self.github_token = self._get_env_var('GITHUB_TOKEN')
        self.owner = self._get_env_var('GITHUB_REPOSITORY_OWNER')
        self.repo = self._get_env_var('GITHUB_REPOSITORY').split('/')[-1]
        self.project_number = int(self._get_env_var('PROJECT_NUMBER'))
        self.rss_url = self._get_env_var('MANTIS_RSS_URL')
        
        # 선택적 환경변수 (기본값 제공)
        self.default_status = os.getenv('DEFAULT_STATUS', 'Todo')
        self.default_milestone = os.getenv('DEFAULT_MILESTONE', '')
        self.status_field_name = os.getenv('STATUS_FIELD_NAME', 'Status')
        self.milestone_field_name = os.getenv('MILESTONE_FIELD_NAME', 'Milestone')
        
        self.api = GitHubProjectsAPI(self.github_token, self.owner, self.repo)
        
    def _get_env_var(self, name: str) -> str:
        """환경 변수 조회 (필수)"""
        value = os.getenv(name)
        if not value:
            logger.error(f"필수 환경변수 {name}이 설정되지 않았습니다.")
            sys.exit(1)
        return value
    
    def parse_rss_feed(self) -> List[Dict]:
        """RSS 피드 파싱"""
        try:
            logger.info(f"RSS 피드 파싱 중: {self.rss_url}")
            feed = feedparser.parse(self.rss_url)
            
            if feed.bozo:
                logger.warning(f"RSS 피드 파싱 경고: {feed.bozo_exception}")
            
            items = []
            for entry in feed.entries:
                # RSS 항목에서 제목과 링크 추출
                title = getattr(entry, 'title', 'No title')
                link = getattr(entry, 'link', '')
                description = getattr(entry, 'description', '')
                
                items.append({
                    'title': title,
                    'url': link,
                    'description': description
                })
            
            logger.info(f"RSS에서 {len(items)}개 항목 파싱 완료")
            return items
            
        except Exception as e:
            logger.error(f"RSS 피드 파싱 실패: {e}")
            return []
    
    def find_field_by_name(self, fields: List[Dict], field_name: str) -> Optional[Dict]:
        """필드 이름으로 필드 정보 찾기"""
        if not fields:
            return None
            
        for field in fields:
            if field and field.get('name') == field_name:
                return field
        return None
    
    def find_option_by_name(self, field: Dict, option_name: str) -> Optional[str]:
        """SingleSelect 필드에서 옵션 이름으로 옵션 ID 찾기"""
        if not field or not field.get('options'):
            return None
            
        for option in field['options']:
            if option and option.get('name') == option_name:
                return option['id']
        return None
    
    def sync_rss_to_project(self):
        """RSS 항목을 GitHub 프로젝트와 동기화"""
        logger.info("RSS to GitHub Projects 동기화 시작")
        
        # 1. 프로젝트 정보 조회
        project_info = self.api.get_project_info(self.project_number)
        if not project_info:
            logger.error("프로젝트 정보를 가져올 수 없습니다.")
            return False
        
        project_id = project_info['id']
        project_fields = project_info.get('fields', {}).get('nodes', [])
        
        logger.info(f"프로젝트 '{project_info['title']}' 정보 조회 완료")
        
        # 2. 필드 정보 파싱
        status_field = self.find_field_by_name(project_fields, self.status_field_name)
        milestone_field = self.find_field_by_name(project_fields, self.milestone_field_name)
        
        # 3. 기존 이슈 목록 조회
        existing_issues = self.api.get_existing_issues(project_id)
        existing_titles = {issue['title'] for issue in existing_issues if issue and issue.get('title')}
        
        # 4. RSS 피드 파싱
        rss_items = self.parse_rss_feed()
        if not rss_items:
            logger.warning("RSS 피드에서 항목을 가져올 수 없습니다.")
            return False
        
        # 5. 신규 항목 처리
        new_items_count = 0
        
        for item in rss_items:
            if not item or not item.get('title'):
                continue
                
            title = item['title']
            
            # 이미 존재하는 이슈인지 확인
            if title in existing_titles:
                logger.debug(f"이슈 이미 존재: {title}")
                continue
            
            # 새 이슈 생성
            issue_body = f"RSS URL: {item.get('url', '')}\n\n{item.get('description', '')}"
            new_issue = self.api.create_issue(title, issue_body)
            
            if not new_issue:
                logger.error(f"이슈 생성 실패: {title}")
                continue
            
            # 프로젝트에 이슈 추가
            project_item_id = self.api.add_issue_to_project(project_id, new_issue['node_id'])
            
            if not project_item_id:
                logger.error(f"프로젝트에 이슈 추가 실패: {title}")
                continue
            
            # 필드 값 설정
            self._update_item_fields(project_id, project_item_id, status_field, milestone_field)
            
            new_items_count += 1
            logger.info(f"신규 이슈 추가 완료: {title}")
        
        logger.info(f"동기화 완료: {new_items_count}개의 신규 이슈 추가")
        return True
    
    def _update_item_fields(self, project_id: str, item_id: str, status_field: Optional[Dict], milestone_field: Optional[Dict]):
        """프로젝트 아이템의 필드 값 업데이트"""
        
        # Status 필드 업데이트
        if status_field and self.default_status:
            if status_field.get('dataType') == 'SINGLE_SELECT':
                status_option_id = self.find_option_by_name(status_field, self.default_status)
                if status_option_id:
                    success = self.api.update_project_item_field(
                        project_id, item_id, status_field['id'], 
                        {'singleSelectOptionId': status_option_id}
                    )
                    if success:
                        logger.debug(f"Status 필드 업데이트 완료: {self.default_status}")
                else:
                    logger.warning(f"Status 옵션을 찾을 수 없습니다: {self.default_status}")
            else:
                # Text 필드인 경우
                success = self.api.update_project_item_field(
                    project_id, item_id, status_field['id'],
                    {'text': self.default_status}
                )
                if success:
                    logger.debug(f"Status 필드 업데이트 완료: {self.default_status}")
        
        # Milestone 필드 업데이트
        if milestone_field and self.default_milestone:
            if milestone_field.get('dataType') == 'SINGLE_SELECT':
                milestone_option_id = self.find_option_by_name(milestone_field, self.default_milestone)
                if milestone_option_id:
                    success = self.api.update_project_item_field(
                        project_id, item_id, milestone_field['id'],
                        {'singleSelectOptionId': milestone_option_id}
                    )
                    if success:
                        logger.debug(f"Milestone 필드 업데이트 완료: {self.default_milestone}")
                else:
                    logger.warning(f"Milestone 옵션을 찾을 수 없습니다: {self.default_milestone}")
            else:
                # Text 필드인 경우
                success = self.api.update_project_item_field(
                    project_id, item_id, milestone_field['id'],
                    {'text': self.default_milestone}
                )
                if success:
                    logger.debug(f"Milestone 필드 업데이트 완료: {self.default_milestone}")

def main():
    """메인 실행 함수"""
    try:
        sync = RSSProjectSync()
        success = sync.sync_rss_to_project()
        
        if success:
            logger.info("RSS to GitHub Projects 동기화가 성공적으로 완료되었습니다.")
            sys.exit(0)
        else:
            logger.error("RSS to GitHub Projects 동기화가 실패했습니다.")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"동기화 중 예상치 못한 오류 발생: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
import logging

from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import PlanRequestSerializer
from .services.errors import PlannerError
from .services.planner import plan_trip

logger = logging.getLogger(__name__)


@api_view(['POST'])
def plan(request):
    serializer = PlanRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({'errors': serializer.errors}, status=400)

    data = serializer.validated_data
    try:
        result = plan_trip(
            origin_text=data['origin'],
            destination_text=data['destination'],
            departure=data['departure'],
            load_lb=data['load_lb'],
            interval_mi=data['interval_mi'],
        )
    except PlannerError as exc:
        logger.warning('Plan failed: %s', exc.message)
        return Response({'errors': {'non_field_errors': [exc.message]}}, status=exc.status)
    return Response(result)


@api_view(['GET'])
def health(request):
    return Response({'status': 'ok'})
